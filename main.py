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
from fastapi.responses import RedirectResponse, Response, FileResponse # הוספנו את FileResponse



load_dotenv()
#os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

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
    # --- החדש ---
    energy_level: int = Field(default=2) # 1=Low, 2=Medium, 3=High
    # ------------
    duration_minutes: int
    target_date: Optional[str] = None
    preferred_time: str
    priority: int
    location_context: str = "Anywhere"
    estimated_transit_minutes: int = 0
    status: str = "new"
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    preferred_start_time: Optional[str] = None

app = FastAPI(title="Aviv's Smart Time Manager")

@app.get("/")
def serve_frontend():
    """מגיש את ממשק המשתמש (HTML) כשנכנסים לכתובת הראשית"""
    return FileResponse("index.html")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"], 
    allow_headers=["*"],
)

#SCOPES = ['https://www.googleapis.com/auth/calendar']
#FRONTEND_URL = "http://127.0.0.1:5500/index.html" 
#oauth_state = {}
SCOPES = ['https://www.googleapis.com/auth/calendar']
RENDER_URL = "https://time-managment-app.onrender.com"
FRONTEND_URL = f"{RENDER_URL}/" # לכאן גוגל תחזיר אותנו
oauth_state = {}

@app.get("/login")
def login_with_google():
    flow = Flow.from_client_secrets_file('credentials.json', scopes=SCOPES, redirect_uri=f'{RENDER_URL}/auth/callback')
    authorization_url, state = flow.authorization_url(access_type='offline', include_granted_scopes='true', prompt='consent')
    oauth_state['state'] = state
    oauth_state['code_verifier'] = getattr(flow, 'code_verifier', None)
    return RedirectResponse(url=authorization_url)

from fastapi import Request
@app.get("/auth/callback")
def auth_callback(request: Request):
    flow = Flow.from_client_secrets_file('credentials.json', scopes=SCOPES, state=oauth_state.get('state'), redirect_uri=f'{RENDER_URL}/auth/callback')
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

    חוקי זמנים ואנרגיה:
    - target_date: תאריך היעד. אם "מחר", הוסף יום. אם צוין יום בשבוע, חשב תאריך. אחרת null.
    - preferred_time: "Morning" (08-12), "Afternoon" (12-17), "Evening" (17-22). אם לא צוין, "Any".
    - energy_level: חלץ ודרג את רמת האנרגיה הנדרשת מ-1 עד 3. 1 = נמוכה (סידורים, מיילים), 2 = בינונית, 3 = גבוהה (למידה אינטנסיבית, כתיבת קוד, פתרון בעיות).

    חוקי מיקומים: "אוניברסיטה" -> "אוניברסיטת תל אביב", "דירה" -> "חובבי ציון 37 תל אביב", "בית" -> "בילו 39 רעננה". אחרת "Anywhere".
    - duration_minutes: הערכת זמן ביצוע.
    - estimated_transit_minutes: דקות נסיעה.
    
    CRITICAL TIME RULES:
    1. If the user specifies an EXACT start time (e.g., "from 15:00", "at 14:30"), set 'preferred_start_time' to that time in 'HH:MM' 24-hour format. Otherwise, set it to null.
    2. If the user specifies a start and end time (e.g., "from 15:00 to 16:30"), mathematically calculate the 'duration_minutes' based on that range (e.g. 90).

    החזר בדיוק את המבנה הזה:
    {{
        "title": "שם המשימה",
        "energy_level": 3,
        "duration_minutes": 60,
        "priority": 2,
        "target_date": "2026-04-02",
        "preferred_time": "Morning",
        "location_context": "Anywhere",
        "estimated_transit_minutes": 0,
        "preferred_start_time": null
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
            
            # הקסם החדש: קורא את כל היומנים המחוברים!
            calendars = service.calendarList().list().execute().get('items', [])
            for cal in calendars:
                try:
                    g_events = service.events().list(calendarId=cal['id'], timeMin=now, timeMax=next_month, singleEvents=True, orderBy='startTime').execute().get('items', [])
                    for ge in g_events:
                        start = ge['start'].get('dateTime', ge['start'].get('date'))
                        end = ge['end'].get('dateTime', ge['end'].get('date'))
                        # צבע כחול לראשי, אפור ליומנים חיצוניים (כמו אוניברסיטה)
                        color = "#4285F4" if cal.get('primary') else "#9E9E9E"
                        events.append({"title": ge.get('summary', 'אירוע'), "start": start, "end": end, "color": color})
                except: pass
        except: pass

    scheduled_tasks = session.exec(select(Task).where(Task.status == "scheduled")).all()
    for t in scheduled_tasks:
        if t.start_time and t.end_time:
            events.append({
                "id": str(t.id),
                "title": f"🤖 {t.title}", 
                "start": t.start_time.isoformat(), 
                "end": t.end_time.isoformat(), 
                "color": "#10B981",
                "extendedProps": {"is_proposed": True}
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
        # קריאת יומן גוגל - הפעם מכל היומנים כדי לא לדרוס אירועים חיצוניים
        fixed_events = []
        if service:
            time_min = start_of_day.astimezone().isoformat()
            time_max = end_of_day.astimezone().isoformat()
            
            calendars = service.calendarList().list().execute().get('items', [])
            all_raw_events = []
            for cal in calendars:
                try:
                    g_events = service.events().list(calendarId=cal['id'], timeMin=time_min, timeMax=time_max, singleEvents=True, orderBy='startTime').execute().get('items', [])
                    all_raw_events.extend(g_events)
                except: pass
            
            # חייבים למיין את כל האירועים מכל היומנים יחד לפי שעת התחלה!
            all_raw_events.sort(key=lambda x: x['start'].get('dateTime', x['start'].get('date', '')))

            for ge in all_raw_events:
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

        # שיבוץ לפי זמנים מועדפים ואנרגיה
        for task in tasks:
            task_duration = timedelta(minutes=task.duration_minutes)
            
            # --- המרת רמת אנרגיה להעדפת זמן ---
            if task.preferred_time == "Any" and not getattr(task, 'preferred_start_time', None):
                if task.energy_level == 3:
                    task.preferred_time = "Morning"
                elif task.energy_level == 1:
                    task.preferred_time = "Evening"
                else:
                    task.preferred_time = "Afternoon"

            # חיתוך החורים הפנויים 
            valid_blocks = []
            for b in free_blocks:
                pref_start, pref_end = start_of_day, end_of_day
                
                # כשיש שעה מדויקת - פותחים את החלון לכל היום! האלגוריתם ימצא את ההכי קרוב מתמטית
                if getattr(task, 'preferred_start_time', None):
                    pass # משאירים את ההתחלה והסוף כקצוות היום
                else:
                    if task.preferred_time == "Morning": pref_end = start_of_day.replace(hour=12)
                    elif task.preferred_time == "Afternoon": pref_start, pref_end = start_of_day.replace(hour=12), start_of_day.replace(hour=17)
                    elif task.preferred_time == "Evening": pref_start = start_of_day.replace(hour=17)
                
                overlap_start, overlap_end = max(b["start"], pref_start), min(b["end"], pref_end)
                if overlap_start < overlap_end:
                    valid_blocks.append({"start": overlap_start, "end": overlap_end, "location": b["location"], "original": b})

            # לוגיקת השיבוץ בפועל - חיפוש סטייה מינימלית
            best_slot = None
            min_delta = timedelta(days=999) # מתחילים מסטייה "אינסופית"

            for vb in valid_blocks:
                transit_mins = task.estimated_transit_minutes if task.location_context != "Anywhere" and vb["location"] != "Anywhere" and task.location_context != vb["location"] else 0
                total_required = task_duration + timedelta(minutes=transit_mins)
                
                # אם החור הפנוי יכול להכיל את המשימה
                if (vb["end"] - vb["start"]) >= total_required:
                    proposed_start = vb["start"] + timedelta(minutes=transit_mins)
                    
                    if getattr(task, 'preferred_start_time', None):
                        try:
                            exact_start = datetime.strptime(f"{date_str} {task.preferred_start_time}", "%Y-%m-%d %H:%M")
                            # מחשבים את המקסימום המותר (מתי המשימה חייבת להתחיל כדי להסתיים בזמן)
                            max_possible_start = vb["end"] - task_duration
                            
                            # נוסחת Clamp: כולאת את שעת היעד בין הקצה המוקדם לקצה המאוחר של החור
                            optimal_in_block = max(proposed_start, min(exact_start, max_possible_start))
                            
                            # חישוב הסטייה המדויקת מהשעה שביקשת
                            delta = abs(optimal_in_block - exact_start)
                            
                            # האם מצאנו מיקום קרוב יותר למה שביקשת?
                            if delta < min_delta:
                                min_delta = delta
                                best_slot = {
                                    "start": optimal_in_block,
                                    "end": optimal_in_block + task_duration,
                                    "vb": vb
                                }
                        except Exception as e:
                            print(e)
                    else:
                        # אין שעה מדויקת - לוקחים את החור הפנוי הראשון שמצאנו
                        best_slot = {
                            "start": proposed_start,
                            "end": proposed_start + task_duration,
                            "vb": vb
                        }
                        break # עוצרים אחרי מציאת המקום הראשון שמתאים

            # אחרי שסרקנו את כל היום, משבצים במנצח (או הראשון שמצאנו, או הכי קרוב מתמטית)
            if best_slot:
                task.start_time = best_slot["start"]
                task.end_time = best_slot["end"]
                task.status = "scheduled"
                session.add(task)
                scheduled_count += 1
                
                # מעדכנים את החור המקורי שלא נדרוס במשימה הבאה
                best_slot["vb"]["original"]["start"] = task.end_time 
                if task.location_context != "Anywhere": best_slot["vb"]["original"]["location"] = task.location_context

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