# SDK Patch for Swipe/Drag Functionality
# Copy this content to sdk/wsconnect.py, replacing the pack_message function
#
# The original pack_message function only supports mm (mouse move), cm (click), ip (input)
# This patch adds touch event support for swipe gestures

"""
Touch Event Protocol (reverse engineered from cg.163.com):
- Event codes: down=1, move=2, up=3 (cancel=4, text=5, key=6, tap=8)
- PC mouse events use different codes: mousedown=100, mouseup=101, mousemove=102, etc.
- Command format: "EVENT_CODE X_COORD Y_COORD POINTER_ID"
- IMPORTANT: Mobile touch coordinates use RAW VIDEO PIXEL values (e.g., 0-1280 for x, 0-720 for y).
  The JS client's send_touchstart/move/end_message functions call this.transform() (display→video
  coords) but NOT getPercentPos(). The 0-65535 normalization (getPercentPos) is ONLY used for
  PC mouse events (codes 100+).

Usage in server.py handle_swipe:
- Build commands directly using the format above
- No need to modify SDK if using inline command construction
"""

# Alternative: Add these cases to pack_message function in sdk/wsconnect.py:
#
# def pack_message(cmd, data):
#     ... existing code ...
#     elif cmd == "press":  # touch press event
#         ptr = data.get("ptr", 0)
#         action = {"id":str(int(round(time.time() * 1000))),"op":"input","data":{"cmd":"1 %d %d %d" % (data["x"], data["y"], ptr)}}
#     elif cmd == "drag":   # touch drag event
#         ptr = data.get("ptr", 0)
#         action = {"id":str(int(round(time.time() * 1000))),"op":"input","data":{"cmd":"2 %d %d %d" % (data["x"], data["y"], ptr)}}
#     elif cmd == "release": # touch release event
#         ptr = data.get("ptr", 0)
#         action = {"id":str(int(round(time.time() * 1000))),"op":"input","data":{"cmd":"3 %d %d %d" % (data["x"], data["y"], ptr)}}
#     ... rest of function ...
