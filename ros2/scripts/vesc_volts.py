import serial, time, pyvesc
from pyvesc.VESC.messages import GetValues

with serial.Serial('/dev/ttyACM0', baudrate=115200, timeout=1.0) as ser:
    for attempt in range(5):
        ser.reset_input_buffer()
        ser.write(pyvesc.encode_request(GetValues))
        time.sleep(0.3)
        buf = ser.read(ser.in_waiting or 128)
        if not buf:
            continue
        try:
            msg, _ = pyvesc.decode(buf)
        except Exception as e:
            print('decode attempt %d failed: %s' % (attempt + 1, e)); continue
        if msg is None:
            continue
        v = msg.v_in
        print('input voltage : %.2f V' % v)
        for cells in (3, 4):
            print('   as %dS -> %.2f V/cell' % (cells, v / cells))
        for f in ('rpm', 'duty_cycle_now', 'avg_motor_current',
                  'avg_input_current', 'temp_fet', 'temp_motor', 'amp_hours'):
            if hasattr(msg, f):
                print('%-18s: %s' % (f, getattr(msg, f)))
        break
    else:
        print('no response from VESC after 5 attempts')
