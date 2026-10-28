import CryptoKit
import Foundation

// webpush: the Web Push pieces the Python standard library lacks.
//   keygen            -> prints a fresh VAPID key pair (RFC 8292) as JSON
//   send              -> reads {vapid, subject, subscription, payload, ttl} from stdin, encrypts the
//                        payload (RFC 8291 aes128gcm) and POSTs it; prints {status, body}
//   encrypt           -> reads {as_private, salt, ua_public, auth, plaintext} from stdin and prints
//                        the encrypted body; exists so tests can check the RFC 8291 known answer
// Every binary field travels as base64url without padding, as the browser APIs hand them out.

struct Failure: Error, CustomStringConvertible { let description: String }

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

func readJSONInput() throws -> [String: Any] {
    let input = FileHandle.standardInput.readDataToEndOfFile()
    guard let object = try JSONSerialization.jsonObject(with: input) as? [String: Any] else {
        throw Failure(description: "stdin must hold a JSON object")
    }
    return object
}

func string(_ object: [String: Any], _ path: String...) throws -> String {
    var current: Any = object
    for key in path {
        guard let dict = current as? [String: Any], let next = dict[key] else {
            throw Failure(description: "missing \(path.joined(separator: "."))")
        }
        current = next
    }
    guard let value = current as? String else { throw Failure(description: "\(path.joined(separator: ".")) must be a string") }
    return value
}

func printJSON(_ object: [String: Any]) throws {
    FileHandle.standardOutput.write(try JSONSerialization.data(withJSONObject: object, options: [.sortedKeys]))
    FileHandle.standardOutput.write(Data("\n".utf8))
}

// RFC 8291 + RFC 8188: one record, aes128gcm, the content key derived from an ECDH agreement
// between a fresh application-server key and the browser's subscription key.
func encrypt(plaintext: Data, uaPublic: Data, auth: Data, asPrivate: P256.KeyAgreement.PrivateKey, salt: Data) throws -> Data {
    let asPublic = asPrivate.publicKey.x963Representation
    let uaKey = try P256.KeyAgreement.PublicKey(x963Representation: uaPublic)
    let shared = try asPrivate.sharedSecretFromKeyAgreement(with: uaKey)
    var keyInfo = Data("WebPush: info\u{0}".utf8)
    keyInfo.append(uaPublic)
    keyInfo.append(asPublic)
    let ikm = shared.hkdfDerivedSymmetricKey(using: SHA256.self, salt: auth, sharedInfo: keyInfo, outputByteCount: 32)
    let cek = HKDF<SHA256>.deriveKey(inputKeyMaterial: ikm, salt: salt, info: Data("Content-Encoding: aes128gcm\u{0}".utf8), outputByteCount: 16)
    let nonce = HKDF<SHA256>.deriveKey(inputKeyMaterial: ikm, salt: salt, info: Data("Content-Encoding: nonce\u{0}".utf8), outputByteCount: 12)
    var record = plaintext
    record.append(0x02)  // last-record delimiter
    let sealed = try AES.GCM.seal(record, using: cek, nonce: AES.GCM.Nonce(data: nonce.withUnsafeBytes { Data($0) }))
    var body = salt
    var rs = UInt32(4096).bigEndian
    body.append(Data(bytes: &rs, count: 4))
    body.append(UInt8(asPublic.count))
    body.append(asPublic)
    body.append(sealed.ciphertext)
    body.append(sealed.tag)
    return body
}

func vapidToken(audience: String, subject: String, privateKey: P256.Signing.PrivateKey) throws -> String {
    let header = base64url(Data("{\"typ\":\"JWT\",\"alg\":\"ES256\"}".utf8))
    let claims: [String: Any] = ["aud": audience, "exp": Int(Date().timeIntervalSince1970) + 12 * 3600, "sub": subject]
    let payload = base64url(try JSONSerialization.data(withJSONObject: claims, options: [.sortedKeys]))
    let signingInput = Data("\(header).\(payload)".utf8)
    let signature = try privateKey.signature(for: signingInput).rawRepresentation  // r || s, as JWS wants
    return "\(header).\(payload).\(base64url(signature))"
}

func send(_ request: [String: Any]) throws -> [String: Any] {
    let endpoint = try string(request, "subscription", "endpoint")
    guard let url = URL(string: endpoint), let scheme = url.scheme, let host = url.host else {
        throw Failure(description: "bad endpoint")
    }
    let audience = scheme + "://" + host + (url.port.map { ":\($0)" } ?? "")
    let uaPublic = try unbase64url(try string(request, "subscription", "keys", "p256dh"))
    let auth = try unbase64url(try string(request, "subscription", "keys", "auth"))
    let vapidPrivate = try P256.Signing.PrivateKey(rawRepresentation: try unbase64url(try string(request, "vapid", "private_key")))
    let vapidPublic = try string(request, "vapid", "public_key")
    guard let payload = request["payload"] else { throw Failure(description: "missing payload") }
    let plaintext = try JSONSerialization.data(withJSONObject: payload, options: [.sortedKeys])
    var salt = Data(count: 16)
    _ = salt.withUnsafeMutableBytes { SecRandomCopyBytes(kSecRandomDefault, 16, $0.baseAddress!) }
    let body = try encrypt(plaintext: plaintext, uaPublic: uaPublic, auth: auth, asPrivate: P256.KeyAgreement.PrivateKey(), salt: salt)

    var http = URLRequest(url: url)
    http.httpMethod = "POST"
    http.httpBody = body
    http.setValue("aes128gcm", forHTTPHeaderField: "Content-Encoding")
    http.setValue("application/octet-stream", forHTTPHeaderField: "Content-Type")
    http.setValue("\(request["ttl"] as? Int ?? 120)", forHTTPHeaderField: "TTL")
    http.setValue("high", forHTTPHeaderField: "Urgency")
    http.setValue("vapid t=\(try vapidToken(audience: audience, subject: try string(request, "subject"), privateKey: vapidPrivate)), k=\(vapidPublic)",
                  forHTTPHeaderField: "Authorization")
    http.timeoutInterval = 20

    var result: [String: Any] = [:]
    var failure: Error?
    let done = DispatchSemaphore(value: 0)
    URLSession.shared.dataTask(with: http) { data, response, error in
        if let error = error { failure = error }
        else if let response = response as? HTTPURLResponse {
            result = ["status": response.statusCode, "body": String(data: data ?? Data(), encoding: .utf8) ?? ""]
        }
        done.signal()
    }.resume()
    done.wait()
    if let failure = failure { throw Failure(description: "request failed: \(failure.localizedDescription)") }
    return result
}

do {
    switch CommandLine.arguments.dropFirst().first {
    case "keygen":
        let key = P256.Signing.PrivateKey()
        try printJSON(["public_key": base64url(key.publicKey.x963Representation), "private_key": base64url(key.rawRepresentation)])
    case "send":
        try printJSON(try send(try readJSONInput()))
    case "encrypt":
        let input = try readJSONInput()
        let asPrivate = try P256.KeyAgreement.PrivateKey(rawRepresentation: try unbase64url(try string(input, "as_private")))
        let body = try encrypt(plaintext: Data(try string(input, "plaintext").utf8),
                               uaPublic: try unbase64url(try string(input, "ua_public")),
                               auth: try unbase64url(try string(input, "auth")),
                               asPrivate: asPrivate, salt: try unbase64url(try string(input, "salt")))
        try printJSON(["body": base64url(body)])
    default:
        throw Failure(description: "usage: webpush keygen | send | encrypt")
    }
} catch {
    FileHandle.standardError.write(Data("webpush: \(error)\n".utf8))
    exit(1)
}
