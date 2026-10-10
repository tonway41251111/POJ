from typing import Optional
import cv2
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import requests
from supabase import Client, create_client

app = FastAPI()

ADMIN_USER = "admin"
ADMIN_PASS = "123456"

SUPABASE_URL = "https://inpjkkpdpbuouxqvnywc.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImlucGpra3BkcGJ1b3V4cXZueXdjIiwicm9sZSI6ImFub24iLCJpYXQiOjE3OTE0Njg1NjAsImV4cCI6MjEwNzA0NDU2MH0.wZm8bGndtFlVoNmmyGkc65o1i2I2OaOkKRRghfO5xS8"
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

LINE_ACCESS_TOKEN = "Gy8ZVPKiucrN3NwLO54qDl2LsQx6egp0gQWTijT23s6pGhz4xM1BS30w9MiefF97tSeAGB9kJa147k+vU1YcnLr0p357ut7fKC0k8m4NZQnaBoyZ+PmO3C3TL62kFdimEXNWX/j9JmFliTuuebpjoQdB04t89/1O/w1cDnyilFU="

MAX_TEMP_THRESHOLD = 38.0
MIN_HUMID_THRESHOLD = 30.0
MAX_HUMID_THRESHOLD = 80.0

# --- Middleware บันทึกคนเข้าเว็บ ---
@app.middleware("http")
async def log_access(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if not path.startswith("/static") and path != "/api/logs":
        ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "unknown").split(",")[0]
        user_agent = request.headers.get("user-agent", "unknown")
        user_id = request.cookies.get("user_id", "guest")
        try:
            supabase.table("access_logs").insert({
                "user_identifier": user_id,
                "ip_address": ip,
                "user_agent": user_agent,
                "path": path
            }).execute()
        except Exception:
            pass
    return response

# --- Schemas ---
class LoginRequest(BaseModel):
    username: str
    password: str

class SignUpRequest(BaseModel):
    first_name: str
    last_name: str
    username: str
    email: str
    password: str
    phone: Optional[str] = None

class ResetPasswordRequest(BaseModel):
    identifier: str  # username หรือ email
    new_password: str

class TelemetryData(BaseModel):
    fall_detected: bool = False
    temperature: Optional[float] = None
    humidity: Optional[float] = None

# --- ฟังก์ชันส่ง LINE Alert ---
def send_line_alert(message: str):
    url = "https://api.line.me/v2/bot/message/broadcast"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_ACCESS_TOKEN}",
    }
    payload = {"messages": [{"type": "text", "text": message}]}
    try:
        requests.post(url, json=payload, headers=headers, timeout=5)
    except Exception:
        pass

# --- กล้อง ---
def generate_frames():
    camera = cv2.VideoCapture(0)
    if not camera.isOpened():
        return
    while True:
        success, frame = camera.read()
        if not success:
            break
        ret, buffer = cv2.imencode(".jpg", frame)
        if not ret:
            break
        yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + buffer.tobytes() + b"\r\n")
    camera.release()

@app.get("/video_feed")
def video_feed():
    return StreamingResponse(generate_frames(), media_type="multipart/x-mixed-replace; boundary=frame")

# --- Telemetry & Logs ---
@app.post("/api/telemetry")
async def receive_api_telemetry(data: TelemetryData):
    alerts = []
    if data.fall_detected:
        alerts.append("🚨 สถานะ: ตรวจพบการล้ม!")
    if data.temperature is not None and data.temperature > MAX_TEMP_THRESHOLD:
        alerts.append(f"🔥 อุณหภูมิ: สูงผิดปกติ ({data.temperature} °C)")
    if data.humidity is not None:
        if data.humidity > MAX_HUMID_THRESHOLD:
            alerts.append(f"💧 ความชื้น: สูงเกินไป ({data.humidity} %)")
        elif data.humidity < MIN_HUMID_THRESHOLD:
            alerts.append(f"⚠️ ความชื้น: ต่ำเกินไป ({data.humidity} %)")

    if alerts:
        msg = f"⚠️ [รายงานแจ้งเตือนระบบ]\n" + "\n".join(alerts) + f"\n\n📊 ข้อมูลปัจจุบัน:\n- อุณหภูมิ: {data.temperature} °C\n- ความชื้น: {data.humidity} %"
        send_line_alert(msg)

    try:
        supabase.table("sensor_logs").insert({
            "fall_detected": data.fall_detected,
            "temperature": data.temperature,
            "humidity": data.humidity,
        }).execute()
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/logs")
def get_logs():
    try:
        res = supabase.table("sensor_logs").select("*").order("id", desc=True).limit(10).execute()
        return res.data
    except Exception:
        return []

# --- Authentication & Password Reset ---
@app.post("/api/login")
def login(data: LoginRequest, response: Response):
    login_id = data.username.strip()
    if login_id == ADMIN_USER and data.password == ADMIN_PASS:
        response.set_cookie(key="logged_in", value="true", max_age=86400, samesite="lax")
        response.set_cookie(key="user_id", value="admin", max_age=86400, samesite="lax")
        return {"status": "success"}

    try:
        user_query = supabase.table("users").select("*").or_(f"username.eq.{login_id},email.eq.{login_id}").eq("password", data.password).execute()
        if user_query.data:
            uname = user_query.data[0].get("username", login_id)
            response.set_cookie(key="logged_in", value="true", max_age=86400, samesite="lax")
            response.set_cookie(key="user_id", value=uname, max_age=86400, samesite="lax")
            return {"status": "success"}
    except Exception:
        pass
    raise HTTPException(status_code=401, detail="ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง")

@app.post("/api/signup")
def register_user(data: SignUpRequest):
    try:
        existing = supabase.table("users").select("id").or_(f"username.eq.{data.username},email.eq.{data.email}").execute()
        if existing.data:
            raise HTTPException(status_code=400, detail="ชื่อผู้ใช้หรืออีเมลนี้มีอยู่ในระบบแล้ว")
        supabase.table("users").insert({
            "first_name": data.first_name,
            "last_name": data.last_name,
            "username": data.username,
            "email": data.email,
            "password": data.password,
            "phone": data.phone,
        }).execute()
        return {"status": "success"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/reset-password")
def reset_password(data: ResetPasswordRequest):
    ident = data.identifier.strip()
    try:
        user_query = supabase.table("users").select("id").or_(f"username.eq.{ident},email.eq.{ident}").execute()
        if not user_query.data:
            raise HTTPException(status_code=404, detail="ไม่พบชื่อผู้ใช้หรืออีเมลนี้ในระบบ")
        
        user_id = user_query.data[0]["id"]
        supabase.table("users").update({"password": data.new_password}).eq("id", user_id).execute()
        return {"status": "success", "message": "เปลี่ยนรหัสผ่านสำเร็จ"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/logout")
def logout(response: Response):
    response.delete_cookie("logged_in")
    response.delete_cookie("user_id")
    return {"status": "success"}

# --- Routes หน้าเว็บ ---
@app.get("/")
def serve_home(request: Request):
    if request.cookies.get("logged_in") != "true":
        return RedirectResponse(url="/login")
    return FileResponse("static/index.html")

@app.get("/login")
@app.get("/login.html")
def serve_login():
    return FileResponse("static/login.html")

@app.get("/signup")
@app.get("/signup.html")
def serve_signup():
    return FileResponse("static/signup.html")

@app.get("/forgot-password")
@app.get("/forgot-password.html")
def serve_forgot_pwd():
    return FileResponse("static/forgot_password.html")

app.mount("/static", StaticFiles(directory="static"), name="static")