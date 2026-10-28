import CoreGraphics
import Foundation
import IOKit
import SystemConfiguration
import os

// pam_macremote: lets the phone approve a sudo while the user is away from the Mac.
//
// Flow: skip (PAM_IGNORE, so the chain moves on to Touch ID and the password) when the user is
// at the console, when sudo was run with -n, or when no passkey is enrolled; otherwise post the
// command to the mac-remote relay, wait for the phone's Face ID assertion, verify it here with
// the root-only key file and return SUCCESS. Timeouts and denials return AUTH_ERR.
//
// Options on the sudo_local line: timeout=<s> (default 120), idle=<s> (default 90), server=<url>.
// Test-only options (used by /etc/pam.d/macremote-test): always=1 skips the at-the-console check,
// debug=1 also prints every log line through the PAM conversation.

let logger = OSLog(subsystem: "mac-remote", category: "pam")
// The PAM result codes come in as Int enum cases; the entry points must return Int32.
let SUCCESS = Int32(PAM_SUCCESS)
let AUTH_ERR = Int32(PAM_AUTH_ERR)
let IGNORE = Int32(PAM_IGNORE)

var debugHandle: OpaquePointer?
let LOG_FILE = "/var/log/mac-remote-pam.log"  // JSON lines, like the server's log; root-written, world-readable

func note(_ message: String) {
    os_log("%{public}s", log: logger, type: .default, message)
    if let pamh = debugHandle { inform(pamh, "[pam_macremote] " + message) }
    let line: [String: Any] = ["ts": isoNow(), "pid": Int(getpid()), "msg": message]
    if let data = try? JSONSerialization.data(withJSONObject: line, options: [.sortedKeys]) {
        let fd = open(LOG_FILE, O_WRONLY | O_APPEND | O_CREAT, 0o644)
        if fd >= 0 {
            var bytes = [UInt8](data)
            bytes.append(0x0A)
            _ = bytes.withUnsafeBytes { write(fd, $0.baseAddress, $0.count) }
            close(fd)
        }
    }
}

struct Options {
    var timeout = 120.0
    var idle = 90.0
    var server = "http://127.0.0.1:18790"
    var always = false
    var debug = false

    init(argc: Int32, argv: UnsafePointer<UnsafePointer<CChar>?>?) {
        guard let argv = argv else { return }
        for index in 0..<Int(argc) {
            guard let pointer = argv[index] else { continue }
            let parts = String(cString: pointer).split(separator: "=", maxSplits: 1).map(String.init)
            guard parts.count == 2 else { continue }
            switch parts[0] {
            case "timeout": timeout = Double(parts[1]) ?? timeout
            case "idle": idle = Double(parts[1]) ?? idle
            case "server": server = parts[1]
            case "always": always = parts[1] == "1"
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

func consoleUser() -> String? {
    var uid: uid_t = 0
    guard let name = SCDynamicStoreCopyConsoleUser(nil, &uid, nil) as String? else { return nil }
    return name
}

func idleSeconds() -> Double? {
    let service = IOServiceGetMatchingService(kIOMainPortDefault, IOServiceMatching("IOHIDSystem"))
    guard service != 0 else { return nil }
    defer { IOObjectRelease(service) }
    guard let value = IORegistryEntryCreateCFProperty(service, "HIDIdleTime" as CFString, kCFAllocatorDefault, 0)?
            .takeRetainedValue() as? Int64 else { return nil }
    return Double(value) / 1_000_000_000
}

func anyDisplayAwake() -> Bool {
    var ids = [CGDirectDisplayID](repeating: 0, count: 16)
    var count: UInt32 = 0
    guard CGGetOnlineDisplayList(UInt32(ids.count), &ids, &count) == .success else { return false }
    return ids.prefix(Int(count)).contains { CGDisplayIsAsleep($0) == 0 }
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
    if !options.always, let console = consoleUser(), console == user, let idle = idleSeconds(), idle < options.idle, anyDisplayAwake() {
        note("\(user) is at the console (idle \(Int(idle))s); leaving it to Touch ID")
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
    inform(pamh, "Waiting for phone approval (up to \(Int(options.timeout))s): \(command.joined(separator: " "))")
    note("approval \(id) requested for \(command.joined(separator: " ")) by \(parentName())")
    let answer: [String: Any]?
    do { answer = try relay.wait(kind: "sudo", id: id, deadline: Date().addingTimeInterval(options.timeout)) } catch {
        note("approval \(id) wait failed: \(error)")
        inform(pamh, "Lost the mac-remote server; no phone approval")
        return AUTH_ERR
    }
    guard let item = answer, let status = item["status"] as? String else {
        note("approval \(id) vanished from the relay")
        return AUTH_ERR
    }
    guard status == "answered", let fields = item["answer"] as? [String: Any] else {
        note("approval \(id) ended as \(status)")
        inform(pamh, status == "denied" ? "Denied on the phone" : "Phone approval timed out")
        return AUTH_ERR
    }
    do {
        let credential = try verify(try Assertion(fields: fields), challenge: challenge, store: store)
        note("approval \(id) verified with passkey \(credential.name) (\(credential.id.prefix(8)))")
        inform(pamh, "Approved on the phone (\(credential.name))")
        return SUCCESS
    } catch {
        note("approval \(id) answer rejected: \(error)")
        inform(pamh, "Phone answer rejected: \(error)")
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
