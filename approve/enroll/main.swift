import Foundation

// macmote-enroll: registers an iPhone passkey for phone-approved sudo. Must run as root
// (`sudo`), because it writes the root-only key file the PAM module trusts.
//
//   enroll --origin https://host:port --name iPhone [--user NAME] [--timeout 300] [--no-qr] [--test]
//   list
//   remove <credential id prefix>
//
// Enrollment opens a one-time window on the relay. The terminal shows a QR code whose link
// carries a pairing token in the URL fragment (never sent to any server) and, as a fallback, a
// six-digit code to type. The page creates the passkey with Face ID and sends the token or code
// back with the public key; the answer is accepted only when it matches and the challenge and
// origin do too. With --test, a real approval request follows and its signature is verified here.

func usage() -> Never {
    FileHandle.standardError.write(Data("usage: macmote-enroll enroll --origin URL --name NAME [--user NAME] [--timeout S] [--no-qr] [--test] | list | remove ID\n".utf8))
    exit(2)
}

func option(_ arguments: [String], _ name: String) -> String? {
    guard let index = arguments.firstIndex(of: name), index + 1 < arguments.count else { return nil }
    return arguments[index + 1]
}

func requireRoot() {
    guard geteuid() == 0 else {
        FileHandle.standardError.write(Data("root is required; run with sudo\n".utf8))
        exit(1)
    }
}

func enroll(_ arguments: [String]) throws {
    requireRoot()
    guard let origin = option(arguments, "--origin"), let name = option(arguments, "--name"),
          let url = URL(string: origin), url.scheme == "https", let host = url.host else { usage() }
    let user = option(arguments, "--user") ?? ProcessInfo.processInfo.environment["SUDO_USER"] ?? NSUserName()
    let timeout = Double(option(arguments, "--timeout") ?? "300") ?? 300
    var store = (try? KeyStore.load()) ?? KeyStore(rpId: host, origin: origin, credentials: [])
    guard store.origin == origin, store.rpId == host else {
        throw Failure(description: "key file is bound to \(store.origin); remove it before enrolling for \(origin)")
    }
    let token = base64url(randomBytes(12))                       // 16 characters, carried by the QR link
    let code = String(format: "%06d", Int.random(in: 0..<1_000_000))  // typed fallback
    let challenge = base64url(randomBytes(32))
    let relay = RelayClient(base: option(arguments, "--server") ?? "http://127.0.0.1:18790")
    let id = try relay.create(kind: "enroll", ["user": user, "challenge": challenge, "rp_id": host, "timeout": Int(timeout)])
    let link = "\(origin)/#pair=\(token)"
    if !arguments.contains("--no-qr") {
        print("")
        try QRCode.print(link)
        print("")
        print("Scan with the iPhone camera; it opens in Safari with the pairing token filled in.")
    }
    print("Or open \(origin)/ in Safari and type the code: \(code)   (within \(Int(timeout))s)")
    guard let item = try relay.wait(kind: "enroll", id: id, deadline: Date().addingTimeInterval(timeout)) else {
        throw Failure(description: "the relay dropped the enrollment")
    }
    guard (item["status"] as? String) == "answered", let fields = item["answer"] as? [String: Any] else {
        throw Failure(description: "enrollment ended as \(item["status"] ?? "?")")
    }
    let credential: Credential
    do {
        credential = try registration(fields: fields, code: token, challenge: challenge, origin: origin, user: user, name: name)
    } catch let first {
        guard "\(first)".contains("code") else { throw first }
        credential = try registration(fields: fields, code: code, challenge: challenge, origin: origin, user: user, name: name)
    }
    store.credentials.removeAll { $0.id == credential.id }
    store.credentials.append(credential)
    try store.save()
    print("Enrolled: \(credential.name) (\(credential.id.prefix(8))…) → \(KEY_FILE)")
    if arguments.contains("--test") {
        try approvalTest(relay: relay, store: store, user: user, timeout: 120)
    }
}

/// Sends a real approval request through the relay and verifies the phone's signature here,
/// exactly as the PAM module would. Run right after enrolment so setup ends with proof.
func approvalTest(relay: RelayClient, store: KeyStore, user: String, timeout: Double) throws {
    let challenge = base64url(randomBytes(32))
    let request: [String: Any] = [
        "command": ["sudo", "echo", "setup test"], "cwd": FileManager.default.currentDirectoryPath, "user": user,
        "pid": Int(getpid()), "ppid": Int(getppid()), "parent": "macmote-enroll (setup test)", "tty": "none",
        "challenge": challenge, "timeout": Int(timeout),
    ]
    let id = try relay.create(kind: "sudo", request)
    print("Approval test: approve the request that just appeared on the phone (within \(Int(timeout))s)")
    guard let item = try relay.wait(kind: "sudo", id: id, deadline: Date().addingTimeInterval(timeout)) else {
        throw Failure(description: "the relay dropped the test request")
    }
    guard (item["status"] as? String) == "answered", let fields = item["answer"] as? [String: Any] else {
        throw Failure(description: "approval test ended as \(item["status"] ?? "?")")
    }
    let credential = try verify(try Assertion(fields: fields), challenge: challenge, store: store)
    print("Approval test passed: signed by \(credential.name) (\(credential.id.prefix(8))…)")
}

func list() throws {
    requireRoot()
    let store = try KeyStore.load()
    print("origin: \(store.origin)   rpId: \(store.rpId)")
    for credential in store.credentials {
        print("\(credential.id)  \(credential.user)  \(credential.name)  \(credential.createdAt)")
    }
}

func remove(_ prefix: String) throws {
    requireRoot()
    var store = try KeyStore.load()
    let before = store.credentials.count
    store.credentials.removeAll { $0.id.hasPrefix(prefix) }
    guard store.credentials.count < before else { throw Failure(description: "no credential starts with \(prefix)") }
    try store.save()
    print("removed \(before - store.credentials.count)")
}

setlinebuf(stdout)  // the code must reach a redirected stdout before the wait begins
let arguments = Array(CommandLine.arguments.dropFirst())
do {
    switch arguments.first {
    case "enroll": try enroll(arguments)
    case "list": try list()
    case "remove": guard arguments.count == 2 else { usage() }; try remove(arguments[1])
    default: usage()
    }
} catch {
    FileHandle.standardError.write(Data("macmote-enroll: \(error)\n".utf8))
    exit(1)
}
