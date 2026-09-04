import rclpy, time
from rclpy.node import Node
from geometry_msgs.msg import Twist
class S(Node):
    def __init__(self):
        super().__init__('steer_check')
        self.pub = self.create_publisher(Twist, '/cmd_vel', 10)
    def send(self, steer, secs):
        m = Twist(); m.linear.x = 0.0; m.angular.z = float(steer)
        t0 = time.time()
        while time.time()-t0 < secs:
            self.pub.publish(m); time.sleep(0.05)
rclpy.init(); n = S(); time.sleep(1.0)
print('5s lead-in, then 4 slow sweeps, THROTTLE STAYS ZERO', flush=True)
n.send(0.0, 5.0)
for i in range(4):
    n.send(-0.8, 1.0); n.send(+0.8, 1.0)
n.send(0.0, 1.5)
for _ in range(20): n.send(0.0, 0.05)
print('done, centred', flush=True)
rclpy.shutdown()
