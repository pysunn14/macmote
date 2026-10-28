import Foundation

// macremote-enroll: registers an iPhone passkey for phone-approved sudo. Must run as root
// (`sudo`), because it writes the root-only key file the PAM module trusts.
//
//   enroll --origin https://host:port --name iPhone [--user NAME] [--timeout 180]
//   list
//   remove <credential id prefix>
//
// Enrollment opens a one-time window on the relay: the page shows the request, the user types the
// code printed here and creates the passkey with Face ID, and the answer is accepted only when the
// code, challenge and origin all match. The code never passes through the server.

func usage() -> Never {
    FileHandle.standardError.write(Data("usage: macremote-enroll enroll --origin URL --name NAME [--user NAME] [--timeout S] | list | remove ID\n".utf8))
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
    let timeout = Double(option(arguments, "--timeout") ?? "180") ?? 180
    var store = (try? KeyStore.load()) ?? KeyStore(rpId: host, origin: origin, credentials: [])
    guard store.origin == origin, store.rpId == host else {
        throw Failure(description: "key file is bound to \(store.origin); remove it before enrolling for \(origin)")
    }
    let code = String(format: "%06d", Int.random(in: 0..<1_000_000))
    let challenge = base64url(randomBytes(32))
    let relay = RelayClient(base: option(arguments, "--server") ?? "http://127.0.0.1:18790")
    let id = try relay.create(kind: "enroll", ["user": user, "challenge": challenge, "rp_id": host, "timeout": Int(timeout)])
    print("On the phone, open \(origin)/ in Safari, type the code and create the passkey with Face ID.")
    print("Enrollment code: \(code)   (within \(Int(timeout))s)")
    guard let item = try relay.wait(kind: "enroll", id: id, deadline: Date().addingTimeInterval(timeout)) else {
        throw Failure(description: "the relay dropped the enrollment")
    }
    guard (item["status"] as? String) == "answered", let fields = item["answer"] as? [String: Any] else {
        throw Failure(description: "enrollment ended as \(item["status"] ?? "?")")
    }
    let credential = try registration(fields: fields, code: code, challenge: challenge, origin: origin, user: user, name: name)
    store.credentials.removeAll { $0.id == credential.id }
    store.credentials.append(credential)
    try store.save()
    print("Enrolled: \(credential.name) (\(credential.id.prefix(8))…) → \(KEY_FILE)")
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
    FileHandle.standardError.write(Data("macremote-enroll: \(error)\n".utf8))
    exit(1)
}
