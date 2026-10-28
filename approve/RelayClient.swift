import Foundation

// Talks to the user-level mac-remote server on loopback: create a request, then poll until the
// phone answers. The answer is verified by the caller, never trusted from the server.

struct RelayClient {
    let base: String  // e.g. http://127.0.0.1:18790

    private func request(_ method: String, _ path: String, body: [String: Any]? = nil, timeout: TimeInterval = 10) throws -> (Int, [String: Any]) {
        guard let url = URL(string: base + path) else { throw Failure(description: "bad url \(path)") }
        var http = URLRequest(url: url)
        http.httpMethod = method
        http.timeoutInterval = timeout
        if let body = body {
            http.httpBody = try JSONSerialization.data(withJSONObject: body)
            http.setValue("application/json", forHTTPHeaderField: "Content-Type")
            http.setValue("1", forHTTPHeaderField: "X-Mac-Remote")
        }
        var status = 0
        var object: [String: Any] = [:]
        var failure: Error?
        let done = DispatchSemaphore(value: 0)
        URLSession.shared.dataTask(with: http) { data, response, error in
            if let error = error { failure = error }
            else if let response = response as? HTTPURLResponse {
                status = response.statusCode
                object = (try? JSONSerialization.jsonObject(with: data ?? Data())) as? [String: Any] ?? [:]
            }
            done.signal()
        }.resume()
        done.wait()
        if let failure = failure { throw Failure(description: "mac-remote unreachable: \(failure.localizedDescription)") }
        return (status, object)
    }

    func create(kind: String, _ body: [String: Any]) throws -> String {
        let (status, object) = try request("POST", "/api/approvals/\(kind)", body: body)
        guard status == 201, let id = object["id"] as? String else {
            throw Failure(description: "create failed (\(status)): \(object["error"] ?? "")")
        }
        return id
    }

    /// Polls until the item leaves `pending` or `deadline` passes. Returns the item (status may
    /// still be pending on timeout) or nil when the server no longer knows it.
    func wait(kind: String, id: String, deadline: Date) throws -> [String: Any]? {
        while true {
            let remaining = deadline.timeIntervalSinceNow
            if remaining <= 0 { return ["status": "timeout"] }
            let slice = Int(min(25, max(1, remaining.rounded(.up))))
            let (status, object) = try request("GET", "/api/approvals/\(kind)/\(id)/wait?timeout=\(slice)", timeout: TimeInterval(slice + 10))
            if status == 404 { return nil }
            guard status == 200 else { throw Failure(description: "wait failed (\(status))") }
            if (object["status"] as? String) != "pending" { return object }
        }
    }
}
