import AppKit
import CoreAudio
import CoreGraphics
import Foundation

// macctl bridges Mac controls that have no public command-line interface:
// - keyboard backlight: CoreBrightness KeyboardBrightnessClient (private)
// - built-in display brightness: DisplayServices (private)
// - lock screen: login.framework SACLockScreenImmediate (private)
// - now playing and transport: MediaRemote (private), with media-key events as the fallback
// - audio output devices: CoreAudio (public)
// When a macOS update removes a private API, the command fails with the missing class or
// symbol name instead of quietly doing nothing, and the server confirms every change by
// reading state back rather than trusting a return value.

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

// MARK: - Audio output devices (CoreAudio, public)

enum AudioOutput {
    private static func address(_ selector: AudioObjectPropertySelector, _ scope: AudioObjectPropertyScope = kAudioObjectPropertyScopeGlobal) -> AudioObjectPropertyAddress {
        AudioObjectPropertyAddress(mSelector: selector, mScope: scope, mElement: kAudioObjectPropertyElementMain)
    }

    private static func get<T>(_ object: AudioObjectID, _ selector: AudioObjectPropertySelector, _ scope: AudioObjectPropertyScope = kAudioObjectPropertyScopeGlobal, default value: T) -> T {
        var address = self.address(selector, scope)
        var size = UInt32(MemoryLayout<T>.size)
        var result = value
        let status = AudioObjectGetPropertyData(object, &address, 0, nil, &size, &result)
        return status == noErr ? result : value
    }

    private static func string(_ object: AudioObjectID, _ selector: AudioObjectPropertySelector) -> String {
        var address = self.address(selector)
        var size = UInt32(MemoryLayout<CFString?>.size)
        var value: Unmanaged<CFString>?
        guard AudioObjectGetPropertyData(object, &address, 0, nil, &size, &value) == noErr, let text = value?.takeRetainedValue() else { return "" }
        return text as String
    }

    private static func transport(_ code: UInt32) -> String {
        switch code {
        case kAudioDeviceTransportTypeBuiltIn: return "builtin"
        case kAudioDeviceTransportTypeUSB: return "usb"
        case kAudioDeviceTransportTypeBluetooth, kAudioDeviceTransportTypeBluetoothLE: return "bluetooth"
        case kAudioDeviceTransportTypeHDMI: return "hdmi"
        case kAudioDeviceTransportTypeDisplayPort: return "displayport"
        case kAudioDeviceTransportTypeAirPlay: return "airplay"
        case kAudioDeviceTransportTypeVirtual: return "virtual"
        case kAudioDeviceTransportTypeAggregate: return "aggregate"
        default: return "other"
        }
    }

    /// Every device with output streams, plus which one is the default.
    static func state() -> [String: Any] {
        var address = self.address(kAudioHardwarePropertyDevices)
        var size: UInt32 = 0
        guard AudioObjectGetPropertyDataSize(AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &size) == noErr else {
            return ["default": NSNull(), "devices": []]
        }
        var ids = [AudioObjectID](repeating: 0, count: Int(size) / MemoryLayout<AudioObjectID>.size)
        AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &size, &ids)
        let current = get(AudioObjectID(kAudioObjectSystemObject), kAudioHardwarePropertyDefaultOutputDevice, default: AudioObjectID(0))
        var devices: [[String: Any]] = []
        for id in ids {
            var streams = self.address(kAudioDevicePropertyStreams, kAudioObjectPropertyScopeOutput)
            var streamSize: UInt32 = 0
            guard AudioObjectGetPropertyDataSize(id, &streams, 0, nil, &streamSize) == noErr, streamSize > 0 else { continue }
            devices.append(["uid": string(id, kAudioDevicePropertyDeviceUID), "name": string(id, kAudioObjectPropertyName),
                            "transport": transport(get(id, kAudioDevicePropertyTransportType, default: UInt32(0)))])
        }
        return ["default": current == 0 ? NSNull() : string(current, kAudioDevicePropertyDeviceUID), "devices": devices]
    }

    static func setDefault(uid: String) throws {
        var address = self.address(kAudioHardwarePropertyDevices)
        var size: UInt32 = 0
        guard AudioObjectGetPropertyDataSize(AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &size) == noErr else {
            throw Failure(description: "cannot list audio devices")
        }
        var ids = [AudioObjectID](repeating: 0, count: Int(size) / MemoryLayout<AudioObjectID>.size)
        AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &size, &ids)
        guard var target = ids.first(where: { string($0, kAudioDevicePropertyDeviceUID) == uid }) else {
            throw Failure(description: "no output device with uid \(uid)")
        }
        var defaultAddress = self.address(kAudioHardwarePropertyDefaultOutputDevice)
        let status = AudioObjectSetPropertyData(AudioObjectID(kAudioObjectSystemObject), &defaultAddress, 0, nil,
                                                UInt32(MemoryLayout<AudioObjectID>.size), &target)
        guard status == noErr else { throw Failure(description: "AudioObjectSetPropertyData returned \(status)") }
    }
}

// MARK: - Media (MediaRemote, private; media keys as the fallback for commands)

enum Media {
    private static let handle = dlopen("/System/Library/PrivateFrameworks/MediaRemote.framework/MediaRemote", RTLD_NOW)

    private static func symbol<T>(_ name: String, as type: T.Type) -> T? {
        guard let handle = handle, let pointer = dlsym(handle, name) else { return nil }
        return unsafeBitCast(pointer, to: type)
    }

    /// Title, artist and whether something is playing; nil when nothing reports.
    static func state() -> Any {
        typealias GetInfo = @convention(c) (DispatchQueue, @escaping ([String: Any]) -> Void) -> Void
        typealias GetPlaying = @convention(c) (DispatchQueue, @escaping (Bool) -> Void) -> Void
        guard let getInfo = symbol("MRMediaRemoteGetNowPlayingInfo", as: GetInfo.self),
              let getPlaying = symbol("MRMediaRemoteGetNowPlayingApplicationIsPlaying", as: GetPlaying.self) else { return NSNull() }
        var info: [String: Any] = [:]
        var playing = false
        let group = DispatchGroup()
        group.enter(); getInfo(DispatchQueue.global()) { info = $0; group.leave() }
        group.enter(); getPlaying(DispatchQueue.global()) { playing = $0; group.leave() }
        _ = group.wait(timeout: .now() + 0.7)
        guard let title = info["kMRMediaRemoteNowPlayingInfoTitle"] as? String, !title.isEmpty else { return NSNull() }
        return ["title": title, "artist": info["kMRMediaRemoteNowPlayingInfoArtist"] as? String ?? "",
                "album": info["kMRMediaRemoteNowPlayingInfoAlbum"] as? String ?? "", "playing": playing,
                "elapsed": info["kMRMediaRemoteNowPlayingInfoElapsedTime"] as? Double ?? 0,
                "duration": info["kMRMediaRemoteNowPlayingInfoDuration"] as? Double ?? 0]
    }

    private static func mediaKey(_ key: Int32) {
        // NX_KEYTYPE_PLAY=16, NX_KEYTYPE_FAST=19, NX_KEYTYPE_REWIND=20: what the keyboard keys send.
        for down in [true, false] {
            let flags = NSEvent.ModifierFlags(rawValue: down ? 0xa00 : 0xb00)
            let data1 = Int((key << 16) | ((down ? 0xa : 0xb) << 8))
            NSEvent.otherEvent(with: .systemDefined, location: .zero, modifierFlags: flags, timestamp: 0, windowNumber: 0,
                               context: nil, subtype: 8, data1: data1, data2: -1)?.cgEvent?.post(tap: .cghidEventTap)
        }
    }

    static func send(_ command: String) throws {
        typealias Send = @convention(c) (Int, AnyObject?) -> Bool
        let commands: [String: (Int, Int32)] = ["toggle": (2, 16), "next": (4, 19), "previous": (5, 20)]   // (MRCommand, media key)
        guard let (code, key) = commands[command] else { throw Failure(description: "usage: macctl media toggle | next | previous") }
        if let send = symbol("MRMediaRemoteSendCommand", as: Send.self), send(code, nil) { return }
        mediaKey(key)
    }
}

func state() -> [String: Any] {
    var out: [String: Any] = [
        "displays": onlineDisplays().map {
            ["id": Int($0), "builtin": CGDisplayIsBuiltin($0) != 0, "asleep": CGDisplayIsAsleep($0) != 0] as [String: Any]
        },
        "locked": isLocked().map { $0 as Any } ?? NSNull(),
        "audio": AudioOutput.state(),
        "media": Media.state(),
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
    case "output":
        guard arguments.count == 2 else { throw Failure(description: "usage: macctl output <device uid>") }
        try AudioOutput.setDefault(uid: arguments[1])
    case "media":
        guard arguments.count == 2 else { throw Failure(description: "usage: macctl media toggle | next | previous") }
        try Media.send(arguments[1])
    default:
        throw Failure(description: "usage: macctl state | keyboard <0-100> | brightness <0-100> | lock | output <uid> | media <toggle|next|previous>")
    }
    let data = try JSONSerialization.data(withJSONObject: state(), options: [.sortedKeys])
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write(Data("\n".utf8))
} catch {
    FileHandle.standardError.write(Data("macctl: \(error)\n".utf8))
    exit(1)
}
