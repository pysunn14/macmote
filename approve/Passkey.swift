import CryptoKit
import Foundation

// Passkey (WebAuthn) verification shared by the PAM module and the enrollment tool.
//
// The public keys live in a root-only file. The user-level server only relays bytes between the
// phone and these root-side tools, so nothing it does can produce a signature that verifies here.

struct Failure: Error, CustomStringConvertible { let description: String }

let KEY_FILE = "/etc/mac-remote/passkeys.json"

func base64url(_ data: Data) -> String {
    data.base64EncodedString().replacingOccurrences(of: "+", with: "-").replacingOccurrences(of: "/", with: "_")
        .replacingOccurrences(of: "=", with: "")
}

func unbase64url(_ text: String) throws -> Data {
    var s = text.replacingOccurrences(of: "-", with: "+").replacingOccurrences(of: "_", with: "/")
    while s.count % 4 != 0 { s += "=" }
    guard let data = Data(base64Encoded: s) else { throw Failure(description: "invalid base64url") }
    return data
}

func randomBytes(_ count: Int) -> Data {
    var data = Data(count: count)
    _ = data.withUnsafeMutableBytes { SecRandomCopyBytes(kSecRandomDefault, count, $0.baseAddress!) }
    return data
}

func isoNow() -> String {
    let formatter = ISO8601DateFormatter()
    formatter.formatOptions = [.withInternetDateTime]
    return formatter.string(from: Date())
}

struct Credential: Codable {
    let id: String          // credential id, base64url
    let publicKey: String   // SubjectPublicKeyInfo DER, base64url (P-256)
    let user: String
    let name: String
    let createdAt: String
}

struct KeyStore: Codable {
    var rpId: String
    var origin: String
    var credentials: [Credential]

    static func load(path: String = KEY_FILE) throws -> KeyStore {
        let data: Data
        do { data = try Data(contentsOf: URL(fileURLWithPath: path)) }
        catch { throw Failure(description: "cannot read \(path): \(error.localizedDescription)") }
        return try JSONDecoder().decode(KeyStore.self, from: data)
    }

    func save(path: String = KEY_FILE) throws {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        let url = URL(fileURLWithPath: path)
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true,
                                                attributes: [.posixPermissions: 0o755])
        let temporary = url.appendingPathExtension("tmp")
        try encoder.encode(self).write(to: temporary, options: [.atomic])
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: temporary.path)
        _ = try FileManager.default.replaceItemAt(url, withItemAt: temporary)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
    }
}

struct ClientData {
    let type: String
    let challenge: String
    let origin: String

    init(json: Data) throws {
        guard let object = try JSONSerialization.jsonObject(with: json) as? [String: Any],
              let type = object["type"] as? String, let challenge = object["challenge"] as? String,
              let origin = object["origin"] as? String else {
            throw Failure(description: "clientDataJSON lacks type, challenge or origin")
        }
        self.type = type
        self.challenge = challenge
        self.origin = origin
    }

    func check(type expectedType: String, challenge expectedChallenge: String, origin expectedOrigin: String) throws {
        guard type == expectedType else { throw Failure(description: "clientData type is \(type), not \(expectedType)") }
        guard challenge == expectedChallenge else { throw Failure(description: "challenge does not match") }
        guard origin == expectedOrigin else { throw Failure(description: "origin \(origin) is not \(expectedOrigin)") }
    }
}

struct Assertion {
    let credentialId: String
    let authenticatorData: Data
    let clientDataJSON: Data
    let signature: Data

    init(fields: [String: Any]) throws {
        func field(_ name: String) throws -> String {
            guard let value = fields[name] as? String else { throw Failure(description: "answer lacks \(name)") }
            return value
        }
        credentialId = try field("credential_id")
        authenticatorData = try unbase64url(try field("authenticator_data"))
        clientDataJSON = try unbase64url(try field("client_data_json"))
        signature = try unbase64url(try field("signature"))
    }
}

/// Checks a WebAuthn assertion against the enrolled keys. Every failure names its reason so a
/// refused sudo can be explained from the log, and a success requires the user-verified flag,
/// which on an iPhone means Face ID or Touch ID happened for this very request.
func verify(_ assertion: Assertion, challenge: String, store: KeyStore) throws -> Credential {
    guard let credential = store.credentials.first(where: { $0.id == assertion.credentialId }) else {
        throw Failure(description: "unknown credential \(assertion.credentialId)")
    }
    try ClientData(json: assertion.clientDataJSON).check(type: "webauthn.get", challenge: challenge, origin: store.origin)
    let authData = assertion.authenticatorData
    guard authData.count >= 37 else { throw Failure(description: "authenticatorData too short") }
    let rpIdHash = Data(SHA256.hash(data: Data(store.rpId.utf8)))
    guard authData.prefix(32) == rpIdHash else { throw Failure(description: "rpIdHash does not match \(store.rpId)") }
    let flags = authData[authData.startIndex + 32]
    guard flags & 0x01 != 0 else { throw Failure(description: "user presence flag not set") }
    guard flags & 0x04 != 0 else { throw Failure(description: "user verification flag not set") }
    let publicKey = try P256.Signing.PublicKey(derRepresentation: try unbase64url(credential.publicKey))
    let signature = try P256.Signing.ECDSASignature(derRepresentation: assertion.signature)
    var signed = authData
    signed.append(Data(SHA256.hash(data: assertion.clientDataJSON)))
    guard publicKey.isValidSignature(signature, for: signed) else { throw Failure(description: "signature invalid") }
    return credential
}

/// Checks a registration answer and returns the credential to store.
func registration(fields: [String: Any], code: String, challenge: String, origin: String, user: String, name: String) throws -> Credential {
    func field(_ key: String) throws -> String {
        guard let value = fields[key] as? String else { throw Failure(description: "answer lacks \(key)") }
        return value
    }
    guard Data(try field("code").utf8) == Data(code.utf8) else { throw Failure(description: "code does not match") }
    try ClientData(json: try unbase64url(try field("client_data_json")))
        .check(type: "webauthn.create", challenge: challenge, origin: origin)
    let publicKey = try field("public_key")
    _ = try P256.Signing.PublicKey(derRepresentation: try unbase64url(publicKey))  // must be P-256
    return Credential(id: try field("credential_id"), publicKey: publicKey, user: user, name: name, createdAt: isoNow())
}
