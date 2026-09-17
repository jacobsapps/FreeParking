import Foundation

/// Normalized, deterministic keyframes shared by the native animation and tests.
/// Positive y is down: the final approach is a reverse into the painted space.
enum CarManoeuvre {
    struct Pose {
        var x: Double = 0
        var y: Double = 0
        var angle: Double = 0
        var opacity: Double = 1
    }

    static func pose(at elapsed: Double, departing: Bool) -> Pose {
        let frames: [(Double, Pose)] = departing ? [
            (0, Pose()),
            (0.65, Pose(x: 0, y: -100, angle: 0)),
            (1.25, Pose(x: 105, y: -135, angle: 85)),
            (1.65, Pose(x: 210, y: -135, angle: 90, opacity: 0))
        ] : [
            (0, Pose(x: 120, y: -100, angle: -85, opacity: 0)),
            (0.25, Pose(x: 48, y: -100, angle: -70)),
            (0.65, Pose(x: -24, y: -80, angle: -20)),
            (1.05, Pose(x: 14, y: -45, angle: 18)),
            (1.65, Pose())
        ]
        guard elapsed > 0 else { return frames[0].1 }
        for i in 1..<frames.count where elapsed <= frames[i].0 {
            let (t0, a) = frames[i - 1], (t1, b) = frames[i]
            let linear = (elapsed - t0) / (t1 - t0)
            let t = linear * linear * (3 - 2 * linear)
            return Pose(x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t,
                        angle: a.angle + (b.angle - a.angle) * t,
                        opacity: a.opacity + (b.opacity - a.opacity) * t)
        }
        return frames.last!.1
    }
}
