import cv2
import requests
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from supabase import create_client, Client
from pydantic import BaseModel
from typing import Optional
from fastapi import FastAPI, HTTPException
from fastapi import Response
from pydantic import BaseModel
class LoginRequest(BaseModel):
  username: str
  password: str
class TelemetryData(BaseModel):
    fall_detected: bool = False
    temperature: Optional[float] = None
    humidity: Optional[float] = None
app = FastAPI()
ADMIN_USER = "admin"
ADMIN_PASS = "123456"
SUPABASE_URL = "https://inpjkkpdpbuouxqvnywc.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImlucGpra3BkcGJ1b3V4cXZueXdjIiwicm9sZSI6ImFub24iLCJpYXQiOjE3OTE0Njg1NjAsImV4cCI6MjEwNzA0NDU2MH0.wZm8bGndtFlVoNmmyGkc65o1i2I2OaOkKRRghfO5xS8"  # วาง key ตัวยาวที่ขึ้นต้นด้วย eyJhbGciOi
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# LINE Messaging API (ใส่ Token เมื่อพร้อมใช้งาน)
LINE_ACCESS_TOKEN = "Gy8ZVPKiucrN3NwLO54qDl2LsQx6egp0gQWTijT23s6pGhz4xM1BS30w9MiefF97tSeAGB9kJa147k+vU1YcnLr0p357ut7fKC0k8m4NZQnaBoyZ+PmO3C3TL62kFdimEXNWX/j9JmFliTuuebpjoQdB04t89/1O/w1cDnyilFU="

def send_line_alert(message: str):
    url = "https://api.line.me/v2/bot/message/broadcast"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_ACCESS_TOKEN}"
    }
    payload = {"messages": [{"type": "text", "text": message}]}
    try:
        res = requests.post(url, json=payload, headers=headers, timeout=5)
        print("ผลลัพธ์ LINE:", res.status_code, res.text)
    except Exception as e:
        print("ส่ง LINE ไม่สำเร็จ:", e)

class SensorPayload(BaseModel):
    fall_detected: bool
    temperature: float
    humidity: float

# สตรีมภาพกล้อง MJPEG
def generate_frames():
    camera = cv2.VideoCapture(0)
    if not camera.isOpened():
      return
    while True:
      success, frame = camera.read()
      if not success:
        break
      ret, buffer = cv2.imencode('.jpg', frame)
      if not ret:
        break
      yield (
          b'--frame\r\n'
          b'Content-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n'
      )
    camera.release()

@app.get("/video_feed")
def video_feed():
    return StreamingResponse(generate_frames(), media_type="multipart/x-mixed-replace; boundary=frame")

# บันทึกข้อมูลลง Supabase Cloud
# กำหนดค่าขีดจำกัดความปลอดภัย
MAX_TEMP_THRESHOLD = 38.0
MIN_HUMID_THRESHOLD = 30.0
MAX_HUMID_THRESHOLD = 80.0

@app.post("/api/telemetry")
async def receive_api_telemetry(data: TelemetryData):
    alerts = []

    # ตรวจสอบสถานะการล้ม
    if data.fall_detected:
        alerts.append("🚨 สถานะ: ตรวจพบการล้ม!")

    # ตรวจสอบอุณหภูมิ
    if data.temperature is not None:
        if data.temperature > MAX_TEMP_THRESHOLD:
            alerts.append(f"🔥 อุณหภูมิ: สูงผิดปกติ ({data.temperature} °C)")

    # ตรวจสอบความชื้น
    if data.humidity is not None:
        if data.humidity > MAX_HUMID_THRESHOLD:
            alerts.append(f"💧 ความชื้น: สูงเกินไป ({data.humidity} %)")
        elif data.humidity < MIN_HUMID_THRESHOLD:
            alerts.append(f"⚠️ ความชื้น: ต่ำเกินไป ({data.humidity} %)")

    # หากมีรายการแจ้งเตือน ให้รวมส่งเป็นชุดเดียวกันในข้อความเดียว
    if alerts:
        message = (
            "⚠️ [รายงานแจ้งเตือนระบบ]\n"
            + "\n".join(alerts)
            + f"\n\n📊 ข้อมูลปัจจุบัน:\n- อุณหภูมิ: {data.temperature} °C\n- ความชื้น: {data.humidity} %"
        )
        send_line_alert(message)

    # บันทึกลงฐานข้อมูล Supabase
    try:
        supabase.table("sensor_logs").insert({
            "fall_detected": data.fall_detected,
            "temperature": data.temperature,
            "humidity": data.humidity
        }).execute()
        return {"status": "success", "message": "Data recorded to Supabase"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
# ดึงข้อมูลจาก Supabase ส่งให้ Dashboard
# ดึงข้อมูลจาก Supabase ส่งให้ Dashboard
# ดึงข้อมูลจาก Supabase ส่งให้ Dashboard
@app.get("/api/logs")
def get_logs():
    try:
        if supabase is None:
            print("Supabase client is None")
            return []
        # เปลี่ยนมาเรียงตาม id จากมากไปน้อย (ล่าสุด)
        response = supabase.table("sensor_logs").select("*").order("id", desc=True).limit(10).execute()
        print(f"Data fetched: {response.data}")
        return response.data
    except Exception as e:
        print(f"Error fetching logs from Supabase: {e}")
        return []
@app.get("/api/health")
def read_root():
    return {"status": "FastAPI is running"}

@app.post("/api/login")
def login(data: LoginRequest, response: Response):
  if data.username == ADMIN_USER and data.password == ADMIN_PASS:
    # ตั้งค่า Cookie เพื่อบันทึกสถานะการล็อกอิน
    response.set_cookie(
        key="logged_in",
        value="true",
        max_age=86400,
        httponly=False,
        samesite="lax",
    )
    return {"status": "success", "message": "เข้าสู่ระบบสำเร็จ"}
  raise HTTPException(status_code=401, detail="ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง")
@app.post("/api/logout")
def logout(response: Response):
  response.delete_cookie("logged_in")
  return {"status": "success"}

app.mount("/", StaticFiles(directory="static", html=True), name="static")