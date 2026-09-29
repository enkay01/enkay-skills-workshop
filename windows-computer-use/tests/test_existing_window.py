import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "client")))
from wcu_client import WcuClient

with WcuClient() as client:
    wins = client.list_windows()
    for w in wins:
        if w.get('is_visible') and w.get('bounds', {}).get('w', 0) > 300 and w.get('title'):
            print(f"HWND: {w['hwnd']}, PID: {w['pid']}, Title: '{w['title']}', Class: '{w['class_name']}', Bounds: {w['bounds']}")
