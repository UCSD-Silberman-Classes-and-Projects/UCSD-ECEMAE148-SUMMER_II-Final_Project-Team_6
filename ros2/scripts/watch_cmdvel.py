import rclpy, time
from rclpy.node import Node
from geometry_msgs.msg import Twist

class W(Node):
    def __init__(self):
        super().__init__('w')
        self.lo_a = self.hi_a = self.lo_l = self.hi_l = 0.0
        self.n = 0
        self.create_subscription(Twist, '/cmd_vel', self.cb, 10)
    def cb(self, m):
        a, l = m.angular.z, m.linear.x
        self.lo_a, self.hi_a = min(self.lo_a, a), max(self.hi_a, a)
        self.lo_l, self.hi_l = min(self.lo_l, l), max(self.hi_l, l)
        self.n += 1

rclpy.init(); n = W()
t0 = time.time()
while time.time() - t0 < 45:
    rclpy.spin_once(n, timeout_sec=0.5)
print('samples=%d  angular.z range [%+.3f .. %+.3f]  linear.x range [%+.3f .. %+.3f]'
      % (n.n, n.lo_a, n.hi_a, n.lo_l, n.hi_l))
print('ALL ZERO -> sliders never reached the node' if (n.hi_a==n.lo_a==0.0 and n.hi_l==n.lo_l==0.0)
      else 'MOVEMENT DETECTED -> node sees the sliders; problem is downstream (VESC power)')
rclpy.shutdown()
