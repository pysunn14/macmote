import CoreGraphics
import Foundation

// macctl bridges Mac controls that have no public command-line interface:
// - keyboard backlight: CoreBrightness KeyboardBrightnessClient
// - built-in display brightness: DisplayServices
// - lock screen: login.framework SACLockScreenImmediate
// All three are private APIs. When a macOS update removes one, the command fails with the
// missing class or symbol name instead of quietly doing nothing, and the server confirms
// every change by reading state back rather than trusting a return value.

struct Failure: Error, CustomStringConvertible {
    let description: String
}

func load(_ path: String) throws -> UnsafeMutableRawPointer {
    guard let handle = dlopen(path, RTLD_NOW) else { throw Failure(description: "cannot load \(path)") }
    return handle
}

func symbol<T>(_ handle: UnsafeMutableRawPointer, _ name: String, as type: T.Type) throws -> T {
    guard let pointer = dlsym(handle, name) else { throw Failure(description: "missing symbol \(name)") }
    return unsafeBitCast(pointer, to: type)
}

func onlineDisplays() -> [CGDirectDisplayID] {
    var ids = [CGDirectDisplayID](repeating: 0, count: 16)
    var count: UInt32 = 0
    guard CGGetOnlineDisplayList(UInt32(ids.count), &ids, &count) == .success else { return [] }
    return Array(ids.prefix(Int(count)))
}

struct Keyboard {
    let client: NSObject
    let id: UInt64

    init() throws {
        _ = try load("/System/Library/PrivateFrameworks/CoreBrightness.framework/CoreBrightness")
        guard let type = NSClassFromString("KeyboardBrightnessClient") as? NSObject.Type else {
            throw Failure(description: "missing class KeyboardBrightnessClient")
        }
        client = type.init()
        let ids = client.perform(NSSelectorFromString("copyKeyboardBacklightIDs"))?.takeRetainedValue() as? [NSNumber]
        guard let first = ids?.first else { throw Failure(description: "no keyboard backlight") }
        id = first.uint64Value
    }

    private func implementation<T>(_ name: String, as type: T.Type) throws -> (Selector, T) {
        let selector = NSSelectorFromString(name)
        guard let method = class_getInstanceMethod(object_getClass(client), selector) else {
            throw Failure(description: "missing method \(name)")
        }
        return (selector, unsafeBitCast(method_getImplementation(method), to: type))
    }

    func brightness() throws -> Float {
        typealias Get = @convention(c) (AnyObject, Selector, UInt64) -> Float
        let (selector, get) = try implementation("brightnessForKeyboard:", as: Get.self)
        return get(client, selector, id)
    }

    func set(_ value: Float) throws {
        // The return type is undocumented; the caller judges success by reading the level back.
        typealias Set = @convention(c) (AnyObject, Selector, Float, UInt64) -> Void
        let (selector, set) = try implementation("setBrightness:forKeyboard:", as: Set.self)
        set(client, selector, value, id)
    }
}

struct BuiltinDisplay {
    typealias Get = @convention(c) (CGDirectDisplayID, UnsafeMutablePointer<Float>) -> Int32
    typealias Set = @convention(c) (CGDirectDisplayID, Float) -> Int32

    let id: CGDirectDisplayID
    let get: Get
    let set: Set

    init() throws {
        let handle = try load("/System/Library/PrivateFrameworks/DisplayServices.framework/DisplayServices")
        get = try symbol(handle, "DisplayServicesGetBrightness", as: Get.self)
        set = try symbol(handle, "DisplayServicesSetBrightness", as: Set.self)
        guard let builtin = onlineDisplays().first(where: { CGDisplayIsBuiltin($0) != 0 }) else {
            throw Failure(description: "built-in display is not online")
        }
        id = builtin
    }

    func brightness() throws -> Float {
        var value: Float = 0
        let status = get(id, &value)
        guard status == 0 else { throw Failure(description: "DisplayServicesGetBrightness returned \(status)") }
        return value
    }

    func apply(_ value: Float) throws {
        let status = set(id, value)
        guard status == 0 else { throw Failure(description: "DisplayServicesSetBrightness returned \(status)") }
    }
}

func lockScreen() throws {
    let handle = try load("/System/Library/PrivateFrameworks/login.framework/Versions/Current/login")
    typealias Lock = @convention(c) () -> Void
    try symbol(handle, "SACLockScreenImmediate", as: Lock.self)()
}

func isLocked() -> Bool? {
    guard let session = CGSessionCopyCurrentDictionary() as? [String: Any] else { return nil }
    return session["CGSSessionScreenIsLocked"] as? Bool ?? false
}

func state() -> [String: Any] {
    var out: [String: Any] = [
        "displays": onlineDisplays().map {
            ["id": Int($0), "builtin": CGDisplayIsBuiltin($0) != 0, "asleep": CGDisplayIsAsleep($0) != 0] as [String: Any]
        },
        "locked": isLocked().map { $0 as Any } ?? NSNull(),
    ]
    do {
        out["keyboard"] = Double(try Keyboard().brightness())
    } catch {
        out["keyboard"] = NSNull()
        out["keyboard_error"] = "\(error)"
    }
    do {
        out["brightness"] = Double(try BuiltinDisplay().brightness())
    } catch {
        out["brightness"] = NSNull()
        out["brightness_error"] = "\(error)"
    }
    return out
}

func percent(_ arguments: [String]) throws -> Float {
    guard arguments.count == 2, let value = Int(arguments[1]), (0...100).contains(value) else {
        throw Failure(description: "value must be an integer from 0 to 100")
    }
    return Float(value) / 100
}

let arguments = Array(CommandLine.arguments.dropFirst())
do {
    switch arguments.first {
    case "state":
        break
    case "keyboard":
        try Keyboard().set(try percent(arguments))
    case "brightness":
        try BuiltinDisplay().apply(try percent(arguments))
    case "lock":
        try lockScreen()
    default:
        throw Failure(description: "usage: macctl state | keyboard <0-100> | brightness <0-100> | lock")
    }
    let data = try JSONSerialization.data(withJSONObject: state(), options: [.sortedKeys])
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write(Data("\n".utf8))
} catch {
    FileHandle.standardError.write(Data("macctl: \(error)\n".utf8))
    exit(1)
}
