import Foundation
import LocalAuthentication
import os

// pam_macremote: lets the phone approve a sudo, wherever the user happens to be.
//
// Flow: skip (PAM_IGNORE, so the chain moves on to Touch ID and the password) when sudo was run
// with -n, when no passkey is enrolled, or when the relay is unreachable. Otherwise post the
// command to the mac-remote relay AND, when a display session is available, show the Mac's own
// Touch ID dialog. Whichever answers first decides: a verified Face ID assertion from the phone
// or a local biometric (or Apple Watch) success returns PAM_SUCCESS and cancels the other side.
// Denials and timeouts return PAM_AUTH_ERR.
//
// Options on the sudo_local line: timeout=<s> (default 120), server=<url>, local=0 (phone only).
// Test-only: debug=1 also prints every log line through the PAM conversation.

let logger = OSLog(subsystem: "mac-remote", category: "pam")
// The PAM result codes come in as Int enum cases; the entry points must return Int32.
let SUCCESS = Int32(PAM_SUCCESS)
let AUTH_ERR = Int32(PAM_AUTH_ERR)
let IGNORE = Int32(PAM_IGNORE)

var debugHandle: OpaquePointer?
let LOG_FILE = "/var/log/mac-remote-pam.log"  // JSON lines, like the server's log; root-written, world-readable
// Opened once while still root: later lines may be written while the effective uid is the user
// (see startLocalPrompt), and an open descriptor keeps working regardless.
let logDescriptor: Int32 = open(LOG_FILE, O_WRONLY | O_APPEND | O_CREAT, 0o644)

func note(_ message: String) {
    os_log("%{public}s", log: logger, type: .default, message)
    if let pamh = debugHandle { inform(pamh, "[pam_macremote] " + message) }
    let line: [String: Any] = ["ts": isoNow(), "pid": Int(getpid()), "msg": message]
    if logDescriptor >= 0, let data = try? JSONSerialization.data(withJSONObject: line, options: [.sortedKeys]) {
        var bytes = [UInt8](data)
        bytes.append(0x0A)
        _ = bytes.withUnsafeBytes { write(logDescriptor, $0.baseAddress, $0.count) }
    }
}

struct Options {
    var timeout = 120.0
    var server = "http://127.0.0.1:18790"
    var local = true
    var debug = false

    init(argc: Int32, argv: UnsafePointer<UnsafePointer<CChar>?>?) {
        guard let argv = argv else { return }
        for index in 0..<Int(argc) {
            guard let pointer = argv[index] else { continue }
            let parts = String(cString: pointer).split(separator: "=", maxSplits: 1).map(String.init)
            guard parts.count == 2 else { continue }
            switch parts[0] {
            case "timeout": timeout = Double(parts[1]) ?? timeout
            case "server": server = parts[1]
            case "local": local = parts[1] != "0"
            case "debug": debug = parts[1] == "1"
            default: break
            }
        }
    }
}

/// argv of this very process (sudo), read from the kernel rather than trusted from the environment.
func processArguments() -> [String] {
    var mib: [Int32] = [CTL_KERN, KERN_PROCARGS2, getpid()]
    var size = 0
    guard sysctl(&mib, 3, nil, &size, nil, 0) == 0, size > 0 else { return [] }
    var buffer = [UInt8](repeating: 0, count: size)
    guard sysctl(&mib, 3, &buffer, &size, nil, 0) == 0 else { return [] }
    let argc = buffer.withUnsafeBytes { $0.load(as: Int32.self) }
    var fields: [String] = []
    var current: [UInt8] = []
    var index = MemoryLayout<Int32>.size
    // After argc comes the executable path, NUL padding, then argc NUL-terminated arguments.
    var seenExecutable = false
    while index < size && fields.count < Int(argc) {
        let byte = buffer[index]
        index += 1
        if byte == 0 {
            if current.isEmpty { continue }
            let text = String(decoding: current, as: UTF8.self)
            current = []
            if !seenExecutable { seenExecutable = true; continue }
            fields.append(text)
        } else {
            current.append(byte)
        }
    }
    return fields
}

/// sudo's own options, stopping at the command so `sudo cat -n file` is not mistaken for `sudo -n`.
func sudoOptions(_ arguments: [String]) -> [String] {
    let takesValue: Set<String> = ["-u", "-g", "-p", "-h", "-C", "-D", "-r", "-t", "-T", "-U"]
    var options: [String] = []
    var skip = false
    for argument in arguments.dropFirst() {
        if skip { skip = false; continue }
        guard argument.hasPrefix("-") else { break }
        if argument == "--" { break }
        options.append(argument)
        skip = takesValue.contains(argument)
    }
    return options
}

func workingDirectory() -> String {
    var info = proc_vnodepathinfo()
    let size = Int32(MemoryLayout<proc_vnodepathinfo>.size)
    guard proc_pidinfo(getpid(), PROC_PIDVNODEPATHINFO, 0, &info, size) == size else { return "?" }
    return withUnsafePointer(to: &info.pvi_cdir.vip_path) { pointer in
        pointer.withMemoryRebound(to: CChar.self, capacity: Int(MAXPATHLEN)) { String(cString: $0) }
    }
}

func parentName() -> String {
    var name = [CChar](repeating: 0, count: 256)
    let length = proc_name(getppid(), &name, UInt32(name.count))
    return length > 0 ? String(cString: name) : "?"
}

/// Outcome of the race between the phone and the local Touch ID dialog.
enum Outcome {
    case phone(Credential)
    case local
    case denied(String)   // reason shown to the user
    case failed(String)   // internal reason, logged
}

/// The Mac's own biometric prompt (Touch ID, or Apple Watch unlock), when a display session
/// exists. Returns nil immediately when it cannot be shown (ssh, no GUI), so the phone is the
/// only path then. The completion runs on LocalAuthentication's own queue.
func startLocalPrompt(reason: String, uid: uid_t, completion: @escaping (Bool) -> Void) -> LAContext? {
    // sudo runs this module with real and effective uid 0, and LocalAuthentication looks up
    // enrolment for the effective user: as root it reports "Biometry is not enrolled". So the
    // context is created and evaluated with the effective uid switched to the invoking user
    // (observed 2026-10: seteuid(user) from root makes canEvaluatePolicy true). The caller
    // restores root once the race is over; the saved uid stays 0, so seteuid(0) is allowed.
    guard seteuid(uid) == 0 else { note("seteuid(\(uid)) failed; no local prompt"); return nil }
    let context = LAContext()
    var error: NSError?
    let policy: LAPolicy = .deviceOwnerAuthenticationWithBiometricsOrCompanion  // Touch ID, or Apple Watch unlock
    guard context.canEvaluatePolicy(policy, error: &error) else {
        note("local prompt unavailable: \(error?.localizedDescription ?? "?")")
        return nil
    }
    context.localizedFallbackTitle = ""  // no password button; the password prompt follows via PAM anyway
    context.evaluatePolicy(policy, localizedReason: reason) { success, error in
        // The reason matters for diagnosis: appCancel means this module withdrew the dialog
        // because the phone answered first; anything else says what the Mac side did.
        if !success { note("local prompt ended: \((error as NSError?).map { "\($0.domain) \($0.code) \($0.localizedDescription)" } ?? "no error")") }
        completion(success)
    }
    note("local prompt shown for uid \(uid)")
    return context
}

func inform(_ pamh: OpaquePointer, _ message: String) {
    // %s needs a C string pointer in the va_list, not a Swift String.
    message.withCString { pointer in
        _ = withVaList([pointer]) { pam_vinfo(pamh, "%s", $0) }
    }
}

func authenticate(_ pamh: OpaquePointer?, options: Options) -> Int32 {
    debugHandle = options.debug ? pamh : nil
    var userPointer: UnsafePointer<CChar>?
    guard let pamh = pamh, pam_get_user(pamh, &userPointer, nil) == SUCCESS, let userPointer = userPointer else {
        note("no PAM user; ignoring")
        return IGNORE
    }
    let user = String(cString: userPointer)
    let arguments = processArguments()
    if sudoOptions(arguments).contains(where: { $0 == "-n" || $0 == "--non-interactive" }) {
        note("sudo -n; ignoring")
        return IGNORE
    }
    let store: KeyStore
    do { store = try KeyStore.load() } catch {
        note("no passkeys: \(error); ignoring")
        return IGNORE
    }
    guard store.credentials.contains(where: { $0.user == user }) else {
        note("no passkey enrolled for \(user); ignoring")
        return IGNORE
    }

    let challenge = base64url(randomBytes(32))
    let command = arguments.isEmpty ? ["sudo"] : arguments
    let request: [String: Any] = [
        "command": command, "cwd": workingDirectory(), "user": user, "pid": Int(getpid()), "ppid": Int(getppid()),
        "parent": parentName(), "tty": ttyname(0).map { String(cString: $0) } ?? "none",
        "challenge": challenge, "timeout": Int(options.timeout),
    ]
    let relay = RelayClient(base: options.server)
    let id: String
    do { id = try relay.create(kind: "sudo", request) } catch {
        note("relay create failed: \(error); ignoring")
        return IGNORE
    }
    let commandText = command.joined(separator: " ")
    note("approval \(id) requested for \(commandText) by \(parentName())")

    // Race: the first side to answer wins; the other is cancelled so no stale prompt lingers.
    let decided = DispatchSemaphore(value: 0)
    let lock = NSLock()
    var outcome: Outcome?
    func settle(_ result: Outcome) {
        lock.lock(); defer { lock.unlock() }
        guard outcome == nil else { return }
        outcome = result
        decided.signal()
    }
    let uid = getpwnam(user)?.pointee.pw_uid
    let local = (options.local && uid != nil) ? startLocalPrompt(reason: "sudo: \(commandText)", uid: uid!) { success in
        if success { settle(.local) }
    } : nil
    inform(pamh, local == nil ? "Approve on the phone (up to \(Int(options.timeout))s): \(commandText)"
                              : "Approve on the phone or with Touch ID (up to \(Int(options.timeout))s): \(commandText)")
    let deadline = Date().addingTimeInterval(options.timeout)
    Thread.detachNewThread {
        do {
            guard let item = try relay.wait(kind: "sudo", id: id, deadline: deadline) else { settle(.failed("vanished from the relay")); return }
            guard let status = item["status"] as? String else { settle(.failed("no status")); return }
            guard status == "answered", let fields = item["answer"] as? [String: Any] else {
                settle(status == "denied" ? .denied("Denied on the phone") : .denied("Phone approval timed out")); return
            }
            do { settle(.phone(try verify(try Assertion(fields: fields), challenge: challenge, store: store))) }
            catch { settle(.failed("answer rejected: \(error)")) }
        } catch { settle(.failed("wait failed: \(error)")) }
    }
    _ = decided.wait(timeout: .now() + options.timeout + 5)
    lock.lock(); let result = outcome ?? .denied("Phone approval timed out"); lock.unlock()
    local?.invalidate()
    _ = seteuid(0)  // back to root for whatever PAM does next
    switch result {
    case .phone(let credential):
        note("approval \(id) verified with passkey \(credential.name) (\(credential.id.prefix(8)))")
        inform(pamh, "Approved on the phone (\(credential.name))")
        return SUCCESS
    case .local:
        // Withdraw synchronously: sudo may exec (or exit, for `sudo -v`) the moment we return,
        // and a detached thread would die with it, leaving a stale card on the phone.
        do { try relay.deny(kind: "sudo", id: id); note("approval \(id) satisfied by local Touch ID; phone request withdrawn") }
        catch { note("approval \(id) satisfied by local Touch ID; could not withdraw the phone request: \(error)") }
        inform(pamh, "Approved with Touch ID")
        return SUCCESS
    case .denied(let reason):
        note("approval \(id) ended: \(reason)")
        inform(pamh, reason)
        return AUTH_ERR
    case .failed(let reason):
        note("approval \(id) failed: \(reason)")
        inform(pamh, "Phone approval failed: \(reason)")
        return AUTH_ERR
    }
}

@_cdecl("pam_sm_authenticate")
public func pam_sm_authenticate(_ pamh: OpaquePointer?, _ flags: Int32, _ argc: Int32,
                                _ argv: UnsafePointer<UnsafePointer<CChar>?>?) -> Int32 {
    return authenticate(pamh, options: Options(argc: argc, argv: argv))
}

@_cdecl("pam_sm_setcred")
public func pam_sm_setcred(_ pamh: OpaquePointer?, _ flags: Int32, _ argc: Int32,
                           _ argv: UnsafePointer<UnsafePointer<CChar>?>?) -> Int32 {
    return SUCCESS
}
