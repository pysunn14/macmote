import Foundation

// QR codes for the terminal, so a phone camera can open the pairing link.
//
// Self-contained encoder (ISO/IEC 18004, byte mode, error correction level M, versions 1–10) so
// the root-side tool depends on no graphics stack: CoreImage's generator proved to draw an empty
// symbol inside virtual machines, and a pairing QR that silently fails is worse than none. The
// self-test decodes the output with Vision on a real Mac; CI checks the symbol's structure.

enum QRCode {
    // Per version, for level M: (data codewords, EC codewords per block, [(blocks, data codewords per block)]).
    private static let versions: [(data: Int, ec: Int, groups: [(Int, Int)])] = [
        (16, 10, [(1, 16)]), (28, 16, [(1, 28)]), (44, 26, [(1, 44)]), (64, 18, [(2, 32)]), (86, 24, [(2, 43)]),
        (108, 16, [(4, 27)]), (124, 18, [(4, 31)]), (154, 22, [(2, 38), (2, 39)]), (182, 22, [(3, 36), (2, 37)]),
        (216, 26, [(4, 43), (1, 44)]),
    ]
    private static let alignment: [[Int]] = [
        [], [6, 18], [6, 22], [6, 26], [6, 30], [6, 34], [6, 22, 38], [6, 24, 42], [6, 26, 46], [6, 28, 50],
    ]

    /// The symbol as a square grid of modules (true = dark), without a quiet zone.
    static func modules(_ text: String) throws -> [[Bool]] {
        let bytes = Array(text.utf8)
        guard let version = (1...versions.count).first(where: { byteCapacity(version: $0) >= bytes.count }) else {
            throw Failure(description: "text too long for a version 10 QR code (\(bytes.count) bytes)")
        }
        let spec = versions[version - 1]
        let size = 17 + 4 * version

        // --- data codewords: mode, count, bytes, terminator, padding ---
        var bits: [Bool] = []
        func append(_ value: Int, _ count: Int) { for i in stride(from: count - 1, through: 0, by: -1) { bits.append((value >> i) & 1 == 1) } }
        append(0b0100, 4)
        append(bytes.count, version <= 9 ? 8 : 16)
        for byte in bytes { append(Int(byte), 8) }
        append(0, min(4, spec.data * 8 - bits.count))
        while bits.count % 8 != 0 { bits.append(false) }
        var data: [UInt8] = stride(from: 0, to: bits.count, by: 8).map { chunk in
            bits[chunk..<chunk + 8].reduce(0) { UInt8($0 << 1) | ($1 ? 1 : 0) }
        }
        var pad: UInt8 = 0xEC
        while data.count < spec.data { data.append(pad); pad = pad == 0xEC ? 0x11 : 0xEC }

        // --- error correction per block, then interleave ---
        var blocks: [[UInt8]] = []
        var offset = 0
        for (count, length) in spec.groups {
            for _ in 0..<count { blocks.append(Array(data[offset..<offset + length])); offset += length }
        }
        let ecBlocks = blocks.map { ReedSolomon.remainder($0, ecCount: spec.ec) }
        var codewords: [UInt8] = []
        for i in 0..<(blocks.map { $0.count }.max() ?? 0) { for block in blocks where i < block.count { codewords.append(block[i]) } }
        for i in 0..<spec.ec { for block in ecBlocks { codewords.append(block[i]) } }

        // --- function patterns ---
        var grid = [[Bool]](repeating: [Bool](repeating: false, count: size), count: size)
        var reserved = [[Bool]](repeating: [Bool](repeating: false, count: size), count: size)
        func set(_ x: Int, _ y: Int, _ dark: Bool) { grid[y][x] = dark; reserved[y][x] = true }
        func finder(_ ox: Int, _ oy: Int) {
            for dy in -1...7 { for dx in -1...7 {
                let x = ox + dx, y = oy + dy
                guard x >= 0, y >= 0, x < size, y < size else { continue }
                let ring = max(abs(dx - 3), abs(dy - 3))
                set(x, y, ring == 0 || ring == 1 || ring == 3)   // dark core, light ring, dark border, light separator
            } }
        }
        finder(0, 0); finder(size - 7, 0); finder(0, size - 7)
        for i in 8..<(size - 8) { if !reserved[6][i] { set(i, 6, i % 2 == 0) }; if !reserved[i][6] { set(6, i, i % 2 == 0) } }
        let centres = alignment[version - 1]
        for cy in centres { for cx in centres {
            // Only the three that would sit on a finder are omitted; the ones on the timing
            // lines are drawn (their middle row and column coincide with the timing pattern).
            let onFinder = (cx <= 8 && cy <= 8) || (cx >= size - 9 && cy <= 8) || (cx <= 8 && cy >= size - 9)
            if onFinder { continue }
            for dy in -2...2 { for dx in -2...2 { set(cx + dx, cy + dy, max(abs(dx), abs(dy)) != 1) } }
        } }
        set(8, size - 8, true)   // the always-dark module
        for i in 0..<9 { reserved[8][min(i, size - 1)] = true; reserved[min(i, size - 1)][8] = true }
        for i in 0..<8 { reserved[8][size - 1 - i] = true; reserved[size - 1 - i][8] = true }
        if version >= 7 { for i in 0..<6 { for j in 0..<3 { reserved[i][size - 11 + j] = true; reserved[size - 11 + j][i] = true } } }

        // --- place data in the zigzag, skipping the vertical timing column ---
        var bitIndex = 0
        let total = codewords.count * 8
        var column = size - 1
        var upward = true
        while column > 0 {
            if column == 6 { column -= 1 }
            for step in 0..<size {
                let y = upward ? size - 1 - step : step
                for x in [column, column - 1] where !reserved[y][x] {
                    let bit = bitIndex < total && (codewords[bitIndex / 8] >> (7 - bitIndex % 8)) & 1 == 1
                    grid[y][x] = bit
                    bitIndex += 1
                }
            }
            upward.toggle()
            column -= 2
        }

        // --- choose the mask with the lowest penalty, then write format and version info ---
        func masked(_ mask: Int) -> [[Bool]] {
            var out = grid
            for y in 0..<size { for x in 0..<size where !reserved[y][x] {
                let flip: Bool
                switch mask {
                case 0: flip = (x + y) % 2 == 0
                case 1: flip = y % 2 == 0
                case 2: flip = x % 3 == 0
                case 3: flip = (x + y) % 3 == 0
                case 4: flip = (y / 2 + x / 3) % 2 == 0
                case 5: flip = (x * y) % 2 + (x * y) % 3 == 0
                case 6: flip = ((x * y) % 2 + (x * y) % 3) % 2 == 0
                default: flip = ((x + y) % 2 + (x * y) % 3) % 2 == 0
                }
                if flip { out[y][x].toggle() }
            } }
            writeFormat(&out, mask: mask, size: size)
            if version >= 7 { writeVersion(&out, version: version, size: size) }
            return out
        }
        var best: [[Bool]] = []
        var bestPenalty = Int.max
        for mask in 0..<8 {
            let candidate = masked(mask)
            let penalty = self.penalty(candidate)
            if penalty < bestPenalty { bestPenalty = penalty; best = candidate }
        }
        return best
    }

    static func byteCapacity(version: Int) -> Int {
        let data = versions[version - 1].data
        return data - (version <= 9 ? 2 : 3)   // mode (4 bits) + count (8 or 16 bits), rounded up to bytes
    }

    private static func writeFormat(_ grid: inout [[Bool]], mask: Int, size: Int) {
        let data = (0b00 << 3) | mask   // level M = 00
        var value = data << 10
        for i in stride(from: 14, through: 10, by: -1) where (value >> i) & 1 == 1 { value ^= 0x537 << (i - 10) }
        let format = ((data << 10) | value) ^ 0x5412
        let bit = { (i: Int) -> Bool in (format >> i) & 1 == 1 }
        // Around the top-left finder.
        let left: [(Int, Int)] = [(8, 0), (8, 1), (8, 2), (8, 3), (8, 4), (8, 5), (8, 7), (8, 8), (7, 8), (5, 8), (4, 8), (3, 8), (2, 8), (1, 8), (0, 8)]
        for (i, (x, y)) in left.enumerated() { grid[y][x] = bit(14 - i) }
        // Split between the other two finders.
        for i in 0..<8 { grid[8][size - 1 - i] = bit(i) }
        for i in 8..<15 { grid[size - 15 + i][8] = bit(i) }
    }

    private static func writeVersion(_ grid: inout [[Bool]], version: Int, size: Int) {
        var value = version << 12
        for i in stride(from: 17, through: 12, by: -1) where (value >> i) & 1 == 1 { value ^= 0x1F25 << (i - 12) }
        let info = (version << 12) | value
        for i in 0..<18 {
            let dark = (info >> i) & 1 == 1
            grid[i / 3][size - 11 + i % 3] = dark
            grid[size - 11 + i % 3][i / 3] = dark
        }
    }

    private static func penalty(_ grid: [[Bool]]) -> Int {
        let size = grid.count
        var score = 0
        for line in 0..<size {   // runs of five or more in rows and columns
            for row in [true, false] {
                var run = 1
                for i in 1..<size {
                    let a = row ? grid[line][i] : grid[i][line], b = row ? grid[line][i - 1] : grid[i - 1][line]
                    if a == b { run += 1; if run == 5 { score += 3 } else if run > 5 { score += 1 } } else { run = 1 }
                }
            }
        }
        for y in 0..<(size - 1) { for x in 0..<(size - 1) {   // 2x2 blocks
            let v = grid[y][x]
            if grid[y][x + 1] == v && grid[y + 1][x] == v && grid[y + 1][x + 1] == v { score += 3 }
        } }
        let pattern: [Bool] = [true, false, true, true, true, false, true, false, false, false, false]
        for line in 0..<size { for i in 0...(size - 11) {   // finder-like sequences
            var rowHit = true, columnHit = true, rowHitReversed = true, columnHitReversed = true
            for k in 0..<11 {
                if grid[line][i + k] != pattern[k] { rowHit = false }
                if grid[line][i + k] != pattern[10 - k] { rowHitReversed = false }
                if grid[i + k][line] != pattern[k] { columnHit = false }
                if grid[i + k][line] != pattern[10 - k] { columnHitReversed = false }
            }
            score += (rowHit ? 40 : 0) + (rowHitReversed ? 40 : 0) + (columnHit ? 40 : 0) + (columnHitReversed ? 40 : 0)
        } }
        let dark = grid.reduce(0) { $0 + $1.filter { $0 }.count }
        let percent = dark * 100 / (size * size)
        score += min(abs(percent - 50) / 5, abs(percent - 50 + 4) / 5) * 10
        return score
    }

    static func terminalLines(_ grid: [[Bool]], quiet: Int = 2) -> [String] {
        let size = grid.count
        let span = size + quiet * 2
        func dark(_ x: Int, _ y: Int) -> Bool {
            let gx = x - quiet, gy = y - quiet
            return gx >= 0 && gy >= 0 && gx < size && gy < size && grid[gy][gx]
        }
        var lines: [String] = []
        var y = 0
        while y < span {
            var line = ""
            for x in 0..<span {
                let top = dark(x, y), bottom = y + 1 < span ? dark(x, y + 1) : false
                switch (top, bottom) {
                case (false, false): line += "█"
                case (false, true): line += "▀"
                case (true, false): line += "▄"
                case (true, true): line += " "
                }
            }
            lines.append(line)
            y += 2
        }
        return lines
    }

    static func print(_ text: String) throws {
        for line in terminalLines(try modules(text)) { Swift.print(line) }
    }
}

/// Reed–Solomon over GF(2^8) with the QR generator polynomial x^8 + x^4 + x^3 + x^2 + 1.
enum ReedSolomon {
    private static let (exp, log): ([UInt8], [Int]) = {
        var exp = [UInt8](repeating: 0, count: 512)
        var log = [Int](repeating: 0, count: 256)
        var value = 1
        for i in 0..<255 {
            exp[i] = UInt8(value)
            log[value] = i
            value <<= 1
            if value & 0x100 != 0 { value ^= 0x11D }
        }
        for i in 255..<512 { exp[i] = exp[i - 255] }
        return (exp, log)
    }()

    private static func multiply(_ a: UInt8, _ b: UInt8) -> UInt8 {
        a == 0 || b == 0 ? 0 : exp[log[Int(a)] + log[Int(b)]]
    }

    static func remainder(_ data: [UInt8], ecCount: Int) -> [UInt8] {
        var generator: [UInt8] = [1]
        for i in 0..<ecCount {
            var next = [UInt8](repeating: 0, count: generator.count + 1)
            for (j, coefficient) in generator.enumerated() {
                next[j] ^= coefficient
                next[j + 1] ^= multiply(coefficient, exp[i])
            }
            generator = next
        }
        var remainder = [UInt8](repeating: 0, count: ecCount)
        for byte in data {
            let factor = byte ^ remainder[0]
            remainder.removeFirst()
            remainder.append(0)
            for (j, coefficient) in generator.dropFirst().enumerated() { remainder[j] ^= multiply(coefficient, factor) }
        }
        return remainder
    }
}
