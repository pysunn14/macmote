import AppKit

// Renders the Home Screen icons into the directory given as the first argument:
// an opaque gradient square (iOS applies its own corner mask) with a white laptop symbol.
let output = URL(fileURLWithPath: CommandLine.arguments[1])
for size in [180, 512] {
    let side = CGFloat(size)
    guard let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: size, pixelsHigh: size, bitsPerSample: 8,
                                        samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB,
                                        bytesPerRow: 0, bitsPerPixel: 0) else { fatalError("cannot allocate bitmap") }
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: bitmap)
    let square = NSRect(x: 0, y: 0, width: side, height: side)
    NSGradient(colors: [NSColor(srgbRed: 0.10, green: 0.10, blue: 0.30, alpha: 1),
                        NSColor(srgbRed: 0.35, green: 0.27, blue: 0.85, alpha: 1),
                        NSColor(srgbRed: 0.04, green: 0.55, blue: 1.00, alpha: 1)])!.draw(in: square, angle: 55)
    NSGradient(colors: [NSColor(white: 1, alpha: 0.30), NSColor(white: 1, alpha: 0)])!
        .draw(in: NSBezierPath(ovalIn: NSRect(x: -side * 0.25, y: side * 0.35, width: side * 1.1, height: side)),
              relativeCenterPosition: NSPoint(x: -0.2, y: 0.3))
    let configuration = NSImage.SymbolConfiguration(pointSize: side * 0.38, weight: .medium)
        .applying(NSImage.SymbolConfiguration(paletteColors: [.white]))
    if let laptop = NSImage(systemSymbolName: "laptopcomputer", accessibilityDescription: nil)?
        .withSymbolConfiguration(configuration) {
        let box = laptop.size
        laptop.draw(in: NSRect(x: (side - box.width) / 2, y: (side - box.height) / 2, width: box.width, height: box.height))
    }
    NSGraphicsContext.restoreGraphicsState()
    try! bitmap.representation(using: .png, properties: [:])!.write(to: output.appendingPathComponent("icon-\(size).png"))
}
