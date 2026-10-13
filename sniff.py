import sys, time, serial
port = sys.argv[1]
for baud in (9600, 57600, 115200):
    print(f"--- {port} @ {baud}")
    try:
        s = serial.Serial(port, baud, timeout=0.5)
    except Exception as e:
        print("OPEN FAILED:", e); break
    end, n = time.time() + 8, 0
    while time.time() < end:
        d = s.read(256)
        if d:
            n += len(d); print(repr(d))
    s.close()
    print("bytes received:", n)