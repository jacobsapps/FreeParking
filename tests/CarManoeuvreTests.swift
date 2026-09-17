import Foundation

@main
struct CarManoeuvreTests {
    static func main() {
        let arrived = CarManoeuvre.pose(at: 2, departing: false)
        precondition(arrived.x == 0 && arrived.y == 0 && arrived.angle == 0 && arrived.opacity == 1)
        let departed = CarManoeuvre.pose(at: 2, departing: true)
        precondition(departed.opacity == 0 && departed.x > 150)
        let turning = CarManoeuvre.pose(at: 0.65, departing: false)
        let reversing = CarManoeuvre.pose(at: 1.05, departing: false)
        precondition(turning.x < 0 && reversing.x > 0 && reversing.y > turning.y)
        precondition(turning.angle < 0 && reversing.angle > 0)
        for departure in [false, true] {
            var previous = CarManoeuvre.pose(at: 0, departing: departure)
            for i in 1...200 {
                let pose = CarManoeuvre.pose(at: Double(i) / 100, departing: departure)
                precondition((0...1).contains(pose.opacity))
                precondition(abs(pose.x - previous.x) < 10 && abs(pose.y - previous.y) < 10)
                previous = pose
            }
        }
        print("Animation paths passed: reverse turn, settled arrival, drive-off and continuous interpolation.")
    }
}
