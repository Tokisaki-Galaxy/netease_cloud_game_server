# SDK Patch for Swipe/Drag Functionality
# Copy this content to sdk/wsconnect.py, replacing the pack_message function
#
# The original pack_message function only supports mm (mouse move), cm (click), ip (input)
# This patch adds touch event support for swipe gestures

"""
Touch Event Protocol (reverse engineered from cg.163.com):
- Event codes: press=1, drag=2, release=3
- Command format: "EVENT_CODE X_COORD Y_COORD POINTER_ID"
- Coordinates use raw pixel values (same as existing mm/cm commands)

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
