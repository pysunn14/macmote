import CryptoKit
import Foundation

// passkey-selftest: builds a synthetic passkey registration and assertion with CryptoKit and
// runs them through the same verify() and registration() the PAM module and enroll tool use.
// Exercises the happy path and every refusal reason without a phone.

let origin = "https://mac.example.com:18791"
let rpId = "mac.example.com"
let key = P256.Signing.PrivateKey()
let credentialId = base64url(randomBytes(16))
let spki = base64url(key.publicKey.derRepresentation)

func clientData(type: String, challenge: String, origin: String) -> Data {
    try! JSONSerialization.data(withJSONObject: ["type": type, "challenge": challenge, "origin": origin, "crossOrigin": false])
}

func authData(rpId: String, flags: UInt8) -> Data {
    var data = Data(SHA256.hash(data: Data(rpId.utf8)))
    data.append(flags)
    data.append(contentsOf: [0, 0, 0, 1])
    return data
}

func assertion(challenge: String, rpId: String = rpId, origin: String = origin, flags: UInt8 = 0x05, key: P256.Signing.PrivateKey = key,
               credentialId: String = credentialId, type: String = "webauthn.get") throws -> Assertion {
    let auth = authData(rpId: rpId, flags: flags)
    let client = clientData(type: type, challenge: challenge, origin: origin)
    var signed = auth
    signed.append(Data(SHA256.hash(data: client)))
    let signature = try key.signature(for: signed).derRepresentation
    return try Assertion(fields: ["credential_id": credentialId, "authenticator_data": base64url(auth),
                                  "client_data_json": base64url(client), "signature": base64url(signature)])
}

var failures = 0
func expect(_ name: String, _ body: () throws -> Void, toFailWith reason: String? = nil) {
    do {
        try body()
        if let reason = reason { print("FAIL \(name): expected failure mentioning '\(reason)'"); failures += 1 }
        else { print("ok   \(name)") }
    } catch {
        if let reason = reason, "\(error)".contains(reason) { print("ok   \(name) (refused: \(error))") }
        else { print("FAIL \(name): \(error)"); failures += 1 }
    }
}

// Registration
let challenge = base64url(randomBytes(32))
let registrationFields: [String: Any] = ["code": "123456", "credential_id": credentialId, "public_key": spki,
                                         "client_data_json": base64url(clientData(type: "webauthn.create", challenge: challenge, origin: origin))]
var store = KeyStore(rpId: rpId, origin: origin, credentials: [])
expect("registration accepts matching code, challenge and origin") {
    store.credentials.append(try registration(fields: registrationFields, code: "123456", challenge: challenge, origin: origin, user: "me", name: "iPhone"))
}
expect("registration refuses a wrong code", { _ = try registration(fields: registrationFields, code: "654321", challenge: challenge, origin: origin, user: "me", name: "x") }, toFailWith: "code")
expect("registration refuses a stale challenge", { _ = try registration(fields: registrationFields, code: "123456", challenge: base64url(randomBytes(32)), origin: origin, user: "me", name: "x") }, toFailWith: "challenge")
expect("registration refuses another origin", { _ = try registration(fields: registrationFields, code: "123456", challenge: challenge, origin: "https://evil.example", user: "me", name: "x") }, toFailWith: "origin")

// Store round trip through a temp file with 0600
let temp = NSTemporaryDirectory() + "passkeys-selftest-\(getpid()).json"
expect("key file round trip") {
    try store.save(path: temp)
    let attributes = try FileManager.default.attributesOfItem(atPath: temp)
    guard (attributes[.posixPermissions] as? Int) == 0o600 else { throw Failure(description: "mode is not 0600") }
    let loaded = try KeyStore.load(path: temp)
    guard loaded.credentials.first?.id == credentialId, loaded.origin == origin else { throw Failure(description: "mismatch") }
    try FileManager.default.removeItem(atPath: temp)
}

// Assertions
let c2 = base64url(randomBytes(32))
expect("assertion with Face ID flags verifies") { _ = try verify(try assertion(challenge: c2), challenge: c2, store: store) }
expect("assertion for another challenge is refused", { _ = try verify(try assertion(challenge: base64url(randomBytes(32))), challenge: c2, store: store) }, toFailWith: "challenge")
expect("assertion without user verification is refused", { _ = try verify(try assertion(challenge: c2, flags: 0x01), challenge: c2, store: store) }, toFailWith: "user verification")
expect("assertion from another origin is refused", { _ = try verify(try assertion(challenge: c2, origin: "https://evil.example"), challenge: c2, store: store) }, toFailWith: "origin")
expect("assertion for another rpId is refused", { _ = try verify(try assertion(challenge: c2, rpId: "other.example"), challenge: c2, store: store) }, toFailWith: "rpIdHash")
expect("assertion signed by another key is refused", { _ = try verify(try assertion(challenge: c2, key: P256.Signing.PrivateKey()), challenge: c2, store: store) }, toFailWith: "signature")
expect("assertion from an unknown credential is refused", { _ = try verify(try assertion(challenge: c2, credentialId: base64url(randomBytes(16))), challenge: c2, store: store) }, toFailWith: "unknown credential")
expect("webauthn.create used as an assertion is refused", { _ = try verify(try assertion(challenge: c2, type: "webauthn.create"), challenge: c2, store: store) }, toFailWith: "type")

print(failures == 0 ? "selftest passed" : "selftest FAILED (\(failures))")
exit(failures == 0 ? 0 : 1)
