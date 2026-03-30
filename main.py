import os
import json
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, Response
from sqlmodel import Field, Session, SQLModel, create_engine, select
from typing import Optional, List
from datetime import datetime, timedelta, date
import google.generativeai as genai
from pydantic import BaseModel
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from dotenv import load_dotenv
from collections import defaultdict

load_dotenv()
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

gemini_api_key = os.environ.get("GEMINI_API_KEY") 
if not gemini_api_key:
    raise ValueError("No GEMINI_API_KEY set for application")
genai.configure(api_key=gemini_api_key)

sqlite_file_name = "tasks.db"
sqlite_url = f"sqlite:///{sqlite_file_name}"
engine = create_engine(sqlite_url, connect_args={"check_same_thread": False})

class Task(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    title: str
    energy_level: int
    status: str = "new" 
    start_time: Optional[datetime] = None 
    end_time: Optional[datetime] = None   
    duration_minutes: Optional[int] = 30  
    priority: int = 2          
    location_context: str = "Anywhere" 
    estimated_transit_minutes: int = 0
    # שני השדות החדשים שהוספנו!
    target_date: Optional[str] = None # פורמט YYYY-MM-DD
    preferred_time: str = "Any" # Morning, Afternoon, Evening, Any

app = FastAPI(title="Aviv's Smart Time Manager")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"], 
    allow_headers=["*"],
)

SCOPES = ['https://www.googleapis.com/auth/calendar']
FRONTEND_URL = "http://127.0.0.1:5500/index.html" 
oauth_state = {}

@app.get("/login")
def login_with_google():
    flow = Flow.from_client_secrets_file('credentials.json', scopes=SCOPES, redirect_uri='http://localhost:8000/auth/callback')
    authorization_url, state = flow.authorization_url(access_type='offline', include_granted_scopes='true', prompt='consent')
    oauth_state['state'] = state
    oauth_state['code_verifier'] = getattr(flow, 'code_verifier', None)
    return RedirectResponse(url=authorization_url)

from fastapi import Request
@app.get("/auth/callback")
def auth_callback(request: Request):
    flow = Flow.from_client_secrets_file('credentials.json', scopes=SCOPES, state=oauth_state.get('state'), redirect_uri='http://localhost:8000/auth/callback')
    flow.fetch_token(authorization_response=str(request.url), code_verifier=oauth_state.get('code_verifier'))
    with open('token.json', 'w') as token_file:
        token_file.write(flow.credentials.to_json())
    return RedirectResponse(url=FRONTEND_URL)

@app.on_event("startup")
def on_startup():
    SQLModel.metadata.create_all(engine)

def get_session():
    with Session(engine) as session:
        yield session

@app.get("/tasks", response_model=List[Task])
def get_tasks(session: Session = Depends(get_session)):
    return session.exec(select(Task).where(Task.status == "new")).all()

# מחיקת משימה
@app.delete("/tasks/{task_id}")
def delete_task(task_id: int, session: Session = Depends(get_session)):
    task = session.get(Task, task_id)
    if task:
        session.delete(task)
        session.commit()
    return {"message": "המשימה נמחקה."}

class SmartTaskRequest(BaseModel):
    text: str

@app.post("/smart_add_task")
def smart_add_task(request: SmartTaskRequest, session: Session = Depends(get_session)):
    today_str = date.today().isoformat()
    day_name = date.today().strftime("%A")
    
    prompt = f"""
    אתה מנהל זמן אישי חכם. היום הוא {day_name}, התאריך: {today_str}.
    חלץ את המשימה מהמשפט החופשי והחזר *רק* JSON תקין.

    חוקי זמנים (חדש!):
    - target_date: תאריך היעד בפורמט YYYY-MM-DD. חשב אותו לפי היום ({today_str}). אם המשתמש אומר "מחר", הוסף יום. אם אומר "ביום חמישי", חשב מה התאריך של יום חמישי הקרוב. אם לא ציין מתי, החזר null.
    - preferred_time: חלץ את הזמן המועדף. "Morning" (08-12), "Afternoon" (12-17), "Evening" (17-22). אם לא צוין, החזר "Any".

    חוקי מיקומים: "אוניברסיטה" -> "אוניברסיטת תל אביב", "דירה" -> "חובבי ציון 37 תל אביב", "בית" -> "בילו 39 רעננה". אחרת "Anywhere".
    - duration_minutes: הערכת זמן ביצוע.
    - requires_travel: true אם יוצאים למקום.
    - estimated_transit_minutes: דקות נסיעה ממוצעת ביום יום.

    החזר בדיוק את המבנה הזה:
    {{
        "title": "שם המשימה",
        "energy_level": 1,
        "duration_minutes": 60,
        "priority": 2,
        "target_date": "2026-04-02",
        "preferred_time": "Morning",
        "location_context": "Anywhere",
        "estimated_transit_minutes": 0
    }}
    המשפט של המשתמש: "{request.text}"
    """
    try:
        model = genai.GenerativeModel('gemini-2.5-flash')
        response = model.generate_content(prompt)
        raw_json = response.text.replace('```json', '').replace('```', '').strip()
        task_data = json.loads(raw_json)
        
        new_task = Task(**task_data)
        session.add(new_task)
        session.commit()
        return {"message": "ה-AI פענח את המשימה בהצלחה!"}
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/calendar/events")
def get_calendar_events(session: Session = Depends(get_session)):
    events = []
    if os.path.exists('token.json'):
        try:
            creds = Credentials.from_authorized_user_file('token.json', SCOPES)
            service = build('calendar', 'v3', credentials=creds)
            now = datetime.utcnow().isoformat() + 'Z'
            next_month = (datetime.utcnow() + timedelta(days=30)).isoformat() + 'Z'
            g_events = service.events().list(calendarId='primary', timeMin=now, timeMax=next_month, singleEvents=True, orderBy='startTime').execute().get('items', [])
            for ge in g_events:
                start = ge['start'].get('dateTime', ge['start'].get('date'))
                end = ge['end'].get('dateTime', ge['end'].get('date'))
                events.append({"title": ge.get('summary', 'אירוע'), "start": start, "end": end, "color": "#4285F4"})
        except: pass

    scheduled_tasks = session.exec(select(Task).where(Task.status == "scheduled")).all()
    for t in scheduled_tasks:
        if t.start_time and t.end_time:
            events.append({
                "id": str(t.id), # מוסיפים מזהה כדי שנדע איזה אירוע הזזת
                "title": f"🤖 {t.title}", 
                "start": t.start_time.isoformat(), 
                "end": t.end_time.isoformat(), 
                "color": "#10B981",
                "extendedProps": {"is_proposed": True} # דגל שמזהה שזה אירוע שאפשר לערוך
            })
    return events

@app.post("/schedule_tasks")
def schedule_tasks(session: Session = Depends(get_session)):
    previously_scheduled = session.exec(select(Task).where(Task.status == "scheduled")).all()
    for t in previously_scheduled:
        t.status = "new"
        t.start_time, t.end_time = None, None
        session.add(t)
    session.commit()

    pending_tasks = session.exec(select(Task).where(Task.status == "new").order_by(Task.priority)).all()
    
    # מיון משימות לפי תאריכים
    # מיון משימות לפי תאריכים
    tasks_by_date = defaultdict(list)
    # מיון משימות לפי תאריכים והיגיון דחיפות
    tasks_by_date = defaultdict(list)
    for t in pending_tasks:
        if t.target_date:
            # 1. יש תאריך מוגדר מראש
            date_str = t.target_date 
        elif t.priority == 1:
            # 2. אין תאריך אבל זה דחוף (Priority 1) -> משבצים להיום
            date_str = datetime.now().strftime("%Y-%m-%d")
        else:
            # 3. אין תאריך ולא דחוף -> "בזמן הקרוב" (נתחיל לחפש ממחר)
            date_str = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
            
        tasks_by_date[date_str].append(t)
    scheduled_count = 0
    creds = Credentials.from_authorized_user_file('token.json', SCOPES) if os.path.exists('token.json') else None
    service = build('calendar', 'v3', credentials=creds) if creds else None

    # ריצה על כל יום בנפרד
    for date_str, tasks in tasks_by_date.items():
        try:
            target_date_obj = datetime.strptime(date_str, "%Y-%m-%d")
        except:
            target_date_obj = datetime.now()

        # הגדרת שעות פעילות רגילות
        start_of_day = target_date_obj.replace(hour=8, minute=0, second=0, microsecond=0)
        end_of_day = target_date_obj.replace(hour=22, minute=0, second=0, microsecond=0)

        # הקסם החדש: אם אנחנו משבצים משימות להיום, נקודת ההתחלה היא עכשיו (+10 דקות באפר)
        if target_date_obj.date() == datetime.now().date():
            now_plus_buffer = datetime.now() + timedelta(minutes=10)
            # לוקחים את המאוחר מבין השניים: 8 בבוקר, או עכשיו
            start_of_day = max(start_of_day, now_plus_buffer)
            
            # אם כבר עברנו את 22:00 בלילה, אין טעם לשבץ היום, נדלג
            if start_of_day >= end_of_day:
                continue

        # קריאת יומן גוגל הספציפי ליום הזה
        fixed_events = []
        if service:
            time_min = start_of_day.astimezone().isoformat()
            time_max = end_of_day.astimezone().isoformat()
            g_events = service.events().list(calendarId='primary', timeMin=time_min, timeMax=time_max, singleEvents=True, orderBy='startTime').execute().get('items', [])
            for ge in g_events:
                start_str, end_str = ge['start'].get('dateTime'), ge['end'].get('dateTime')
                if start_str and end_str:
                    fixed_events.append({
                        "start": datetime.fromisoformat(start_str.replace('Z', '+00:00')).replace(tzinfo=None),
                        "end": datetime.fromisoformat(end_str.replace('Z', '+00:00')).replace(tzinfo=None),
                        "location": ge.get('location', 'Anywhere')
                    })

        # יצירת חורים פנויים
        free_blocks = []
        current_time, current_location = start_of_day, "חובבי ציון 37 תל אביב"
        for event in fixed_events:
            block_end = event["start"] - timedelta(minutes=15)
            if current_time < block_end:
                free_blocks.append({"start": current_time, "end": block_end, "location": current_location})
            current_time, current_location = max(current_time, event["end"]), event["location"]
        if current_time < end_of_day:
            free_blocks.append({"start": current_time, "end": end_of_day, "location": current_location})

        # שיבוץ לפי זמנים מועדפים
        for task in tasks:
            task_duration = timedelta(minutes=task.duration_minutes)
            
            # חיתוך החורים הפנויים לפי בוקר/צהריים/ערב
            valid_blocks = []
            for b in free_blocks:
                pref_start, pref_end = start_of_day, end_of_day
                if task.preferred_time == "Morning": pref_end = start_of_day.replace(hour=12)
                elif task.preferred_time == "Afternoon": pref_start, pref_end = start_of_day.replace(hour=12), start_of_day.replace(hour=17)
                elif task.preferred_time == "Evening": pref_start = start_of_day.replace(hour=17)
                
                overlap_start, overlap_end = max(b["start"], pref_start), min(b["end"], pref_end)
                if overlap_start < overlap_end:
                    valid_blocks.append({"start": overlap_start, "end": overlap_end, "location": b["location"], "original": b})

            for vb in valid_blocks:
                transit_mins = task.estimated_transit_minutes if task.location_context != "Anywhere" and vb["location"] != "Anywhere" and task.location_context != vb["location"] else 0
                total_required = task_duration + timedelta(minutes=transit_mins)
                
                if (vb["end"] - vb["start"]) >= total_required:
                    task.start_time = vb["start"] + timedelta(minutes=transit_mins)
                    task.end_time = task.start_time + task_duration
                    task.status = "scheduled"
                    session.add(task)
                    scheduled_count += 1
                    vb["original"]["start"] = task.end_time # מעדכן את החור המקורי שלא נדרוס
                    if task.location_context != "Anywhere": vb["original"]["location"] = task.location_context
                    break 

    session.commit()
    return {"message": f"שובצו {scheduled_count} משימות בהצלחה!"}

@app.post("/sync_to_google")
def sync_to_google(session: Session = Depends(get_session)):
    if not os.path.exists('token.json'): return {"error": "לא מחובר"}
    creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    service = build('calendar', 'v3', credentials=creds)
    tasks_to_sync = session.exec(select(Task).where(Task.status == "scheduled")).all()
    synced_count = 0
    for task in tasks_to_sync:
        event = {
            'summary': f"🤖 {task.title}",
            'location': task.location_context if task.location_context != 'Anywhere' else '',
            'description': f"מועדף: {task.preferred_time}\nזמן נסיעה: {task.estimated_transit_minutes} דק'",
            'start': {'dateTime': task.start_time.isoformat(), 'timeZone': 'Asia/Jerusalem'},
            'end': {'dateTime': task.end_time.isoformat(), 'timeZone': 'Asia/Jerusalem'},
        }
        service.events().insert(calendarId='primary', body=event).execute()
        task.status = "synced"
        session.add(task)
        synced_count += 1
    session.commit()
    return {"message": f"סונכרנו {synced_count} משימות ליומן גוגל!"}
# --- נקודות קצה חדשות לעריכה ידנית מהיומן ---
class UpdateTaskTimeRequest(BaseModel):
    start_time: str
    end_time: str

@app.put("/tasks/{task_id}/time")
def update_task_time(task_id: int, req: UpdateTaskTimeRequest, session: Session = Depends(get_session)):
    task = session.get(Task, task_id)
    if not task:
        return {"error": "לא נמצאה משימה"}
    
    # המרה מהפורמט של הדפדפן לפורמט של פייתון
    task.start_time = datetime.fromisoformat(req.start_time.replace('Z', '+00:00')).replace(tzinfo=None)
    task.end_time = datetime.fromisoformat(req.end_time.replace('Z', '+00:00')).replace(tzinfo=None)
    
    session.add(task)
    session.commit()
    return {"message": "הזמנים עודכנו בהצלחה"}