import Foundation

// pamtest: runs pam_authenticate for the service named on the command line (default
// "macmote-test", see scripts/install-sudo.sh) so the module can be exercised without touching
// sudo. Prints the PAM result code and relays the module's info messages to stdout.

var conversation = pam_conv(conv: { count, messages, responses, _ in
    for index in 0..<Int(count) {
        guard let message = messages?[index] else { continue }
        print("[pam] " + String(cString: message.pointee.msg))
    }
    responses?.pointee = nil
    return Int32(PAM_CONV_ERR)
}, appdata_ptr: nil)

let service = CommandLine.arguments.dropFirst().first ?? "macmote-test"
let user = ProcessInfo.processInfo.environment["SUDO_USER"] ?? NSUserName()
var started: OpaquePointer?
guard pam_start(service, user, &conversation, &started) == Int32(PAM_SUCCESS), let handle = started else {
    print("pam_start failed")
    exit(1)
}
let result = pam_authenticate(handle, 0)
print("pam_authenticate(\(service), \(user)) = \(result) (\(String(cString: pam_strerror(handle, result))))")
pam_end(handle, result)
exit(result == Int32(PAM_SUCCESS) ? 0 : 1)
