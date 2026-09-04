import rclpy, numpy as np, cv2, sys
from rclpy.node import Node
from sensor_msgs.msg import Image

class Grab(Node):
    def __init__(self):
        super().__init__('grab')
        self.done = False
        self.create_subscription(Image, '/camera/color/image_raw', self.cb, 10)

    def cb(self, msg):
        if self.done:
            return
        buf = np.frombuffer(msg.data, dtype=np.uint8)
        enc = msg.encoding.lower()
        if enc in ('bgr8', 'rgb8'):
            img = buf.reshape(msg.height, msg.width, 3)
            if enc == 'rgb8':
                img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
        elif enc == 'mono8':
            img = buf.reshape(msg.height, msg.width)
        else:
            print('unhandled encoding: ' + msg.encoding); sys.exit(1)
        cv2.imwrite('/tmp/track.jpg', img)
        print('saved %dx%d encoding=%s' % (msg.width, msg.height, msg.encoding))
        self.done = True

rclpy.init()
n = Grab()
while rclpy.ok() and not n.done:
    rclpy.spin_once(n, timeout_sec=5.0)
rclpy.shutdown()
