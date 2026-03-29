import os
import json
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi import Request
from fastapi.responses import RedirectResponse, Response
from sqlmodel import Field, Session, SQLModel, create_engine, select
from typing import Optional, List
from datetime import datetime, timedelta
import google.generativeai as genai
from pydantic import BaseModel
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from dotenv import load_dotenv
from fastapi import Request
import traceback

# משתנה גלובלי ששומר את לחיצת היד הסודית של גוגל בין שני המסכים
oauth_state = {}
# טעינת משתני סביבה (לוקאלי)
load_dotenv()

# 1. הגדרת המפתח של ה-AI
gemini_api_key = os.environ.get("GEMINI_API_KEY") 
if not gemini_api_key:
    raise ValueError("No GEMINI_API_KEY set for application")

genai.configure(api_key=gemini_api_key)

# 2. הגדרת מסד הנתונים
sqlite_file_name = "tasks.db"
sqlite_url = f"sqlite:///{sqlite_file_name}"
engine = create_engine(sqlite_url, connect_args={"check_same_thread": False})

class Task(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    title: str
    energy_level: int
    can_be_parallel: bool = False
    is_container: bool = False
    deadline: Optional[datetime] = None
    status: str = "new" # מצבים: new, scheduled, synced
    parent_id: Optional[int] = Field(default=None)
    is_fixed_event: bool = False # שומרים את השדה שלא יקרוס מסד הנתונים הישן
    start_time: Optional[datetime] = None 
    end_time: Optional[datetime] = None   
    duration_minutes: Optional[int] = 30  
    priority: int = 2          
    requires_travel: bool = False
    location_context: str = "Anywhere" 
    estimated_transit_minutes: int = 0

# 3. יצירת השרת
app = FastAPI(title="Aviv's Smart Time Manager")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"], 
    allow_headers=["*"],
)

# ---------------------------------------------------------
# Google Auth - אימות מול גוגל
# ---------------------------------------------------------
SCOPES = ['https://www.googleapis.com/auth/calendar']
# שנה את הפורט פה אם ה-Live Server שלך רץ על מספר אחר (למשל 3000)
FRONTEND_URL = "http://127.0.0.1:5500/index.html" 

@app.get("/login")
def login_with_google():
    """הפונקציה שזורקת אותך למסך ההתחברות של גוגל"""
    flow = Flow.from_client_secrets_file(
        'credentials.json',
        scopes=SCOPES,
        redirect_uri='http://localhost:8000/auth/callback'
    )
    
    authorization_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true',
        prompt='consent'  # <--- זו מילת הקסם שהוספנו! מכריחה את גוגל לתת מפתח חדש
    )
    
    # שומרים בזיכרון את המפתחות שגוגל הולכת לבקש מאיתנו בחזור!
    oauth_state['state'] = state
    oauth_state['code_verifier'] = getattr(flow, 'code_verifier', None)
    
    return RedirectResponse(url=authorization_url)

@app.get("/auth/callback")
def auth_callback(request: Request):
    """הדלת שגוגל מחזירה אליה - עכשיו עם זיכרון פיל!"""
    try:
        os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
        
        # אנחנו מקימים את הקשר מחדש, ונותנים לו את ה-state מהזיכרון
        flow = Flow.from_client_secrets_file(
            'credentials.json',
            scopes=SCOPES,
            state=oauth_state.get('state'),
            redirect_uri='http://localhost:8000/auth/callback'
        )
        
        # שולפים את ה-verifier מהזיכרון ומגישים לגוגל עם הכתובת
        flow.fetch_token(
            authorization_response=str(request.url),
            code_verifier=oauth_state.get('code_verifier')
        )
        
        with open('token.json', 'w') as token_file:
            token_file.write(flow.credentials.to_json())
            
        return RedirectResponse(url=FRONTEND_URL)
        
    except Exception as e:
        import traceback
        return {
            "CRASH_REPORT": "השרת קרס, הנה הסיבה האמיתית:",
            "error_message": str(e),
            "traceback": traceback.format_exc()
        }
        
    except Exception as e:
        # תפסנו את השגיאה! עכשיו נראה אותה ישר בדפדפן במקום מסך שחור
        return {
            "CRASH_REPORT": "השרת קרס, הנה הסיבה האמיתית:",
            "error_message": str(e),
            "traceback": traceback.format_exc()
        }
    

@app.on_event("startup")
def on_startup():
    SQLModel.metadata.create_all(engine)

def get_session():
    with Session(engine) as session:
        yield session

# ---------------------------------------------------------
# APIs למשימות חכמות ומידע ליומן
# ---------------------------------------------------------

@app.get("/tasks", response_model=List[Task])
def get_tasks(session: Session = Depends(get_session)):
    # מציג רק משימות שעוד לא שובצו או סונכרנו לגוגל
    return session.exec(select(Task).where(Task.status == "new")).all()

class SmartTaskRequest(BaseModel):
    text: str

@app.post("/smart_add_task")
def smart_add_task(request: SmartTaskRequest, session: Session = Depends(get_session)):
    prompt = f"""
    אתה מנהל זמן אישי חכם. המשתמש יזרוק לך משפט חופשי. עליך לחלץ את המשימה ולהחזיר *רק* מבנה JSON תקין.
    חוקי מיקומים (קריטי!):
    - המילה "אוניברסיטה" או "קמפוס" -> "אוניברסיטת תל אביב"
    - המילה "דירה" -> "חובבי ציון 37 תל אביב"
    - המילה "בית" או "הורים" -> "בילו 39 רעננה"
    אם אין מיקום ספציפי שמשתמע מהבקשה, ה-location_context יהיה "Anywhere".

    חוקים נוספים:
    - energy_level: 1 (ריכוז), 2 (בינוני), 3 (קליל).
    - duration_minutes: הערכת זמן ביצוע בדקות.
    - priority: 1 (דחוף), 2 (רגיל), 3 (פנאי).
    - requires_travel: true אם המשימה מצריכה יציאה למקום ספציפי.
    - estimated_transit_minutes: דקות נסיעה ממוצעת למיקום הזה ביום יום.

    החזר בדיוק את המבנה הזה:
    {{
        "title": "שם המשימה",
        "energy_level": 1,
        "duration_minutes": 60,
        "priority": 2,
        "requires_travel": true,
        "location_context": "אוניברסיטת תל אביב",
        "estimated_transit_minutes": 45
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
        return {"error": "הייתה בעיה בפענוח המשימה", "details": str(e)}

@app.get("/api/calendar/events")
def get_calendar_events(session: Session = Depends(get_session)):
    """מושך אירועים מגוגל + משימות ששובצו, ושולח לתצוגה ב-HTML"""
    events = []
    
    # 1. מושכים מגוגל (כחול)
    if os.path.exists('token.json'):
        try:
            creds = Credentials.from_authorized_user_file('token.json', SCOPES)
            service = build('calendar', 'v3', credentials=creds)
            now = datetime.utcnow().isoformat() + 'Z'
            next_month = (datetime.utcnow() + timedelta(days=30)).isoformat() + 'Z'
            
            g_events = service.events().list(
                calendarId='primary', timeMin=now, timeMax=next_month,
                singleEvents=True, orderBy='startTime').execute().get('items', [])
            
            for ge in g_events:
                start = ge['start'].get('dateTime', ge['start'].get('date'))
                end = ge['end'].get('dateTime', ge['end'].get('date'))
                events.append({
                    "title": ge.get('summary', 'אירוע'),
                    "start": start,
                    "end": end,
                    "color": "#4285F4" 
                })
        except Exception as e:
            print("Error reading Google Calendar:", e)

    # 2. מושכים את השיבוצים שלנו שעוד לא סונכרנו (ירוק)
    scheduled_tasks = session.exec(select(Task).where(Task.status == "scheduled")).all()
    for t in scheduled_tasks:
        if t.start_time and t.end_time:
            events.append({
                "title": f"🤖 {t.title}",
                "start": t.start_time.isoformat(),
                "end": t.end_time.isoformat(),
                "color": "#10B981" 
            })

    return events

# ---------------------------------------------------------
# אלגוריתם השיבוץ החכם וסנכרון לגוגל
# ---------------------------------------------------------

@app.post("/schedule_tasks")
def schedule_tasks(session: Session = Depends(get_session)):
    """קורא מגוגל את המצב למחר, ומשבץ את המשימות החדשות בחורים"""
    
    # איפוס משימות ששובצו אך לא סונכרנו
    previously_scheduled = session.exec(select(Task).where(Task.status == "scheduled")).all()
    for t in previously_scheduled:
        t.status = "new"
        t.start_time = None
        t.end_time = None
        session.add(t)
    session.commit()

    tomorrow = datetime.now() + timedelta(days=1)
    start_of_day = tomorrow.replace(hour=8, minute=0, second=0, microsecond=0)
    end_of_day = tomorrow.replace(hour=22, minute=0, second=0, microsecond=0)

    # 1. שליפת אירועים קשיחים ישירות מ-Google Calendar!
    fixed_events = []
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
        service = build('calendar', 'v3', credentials=creds)
        time_min = start_of_day.astimezone().isoformat()
        time_max = end_of_day.astimezone().isoformat()
        
        g_events = service.events().list(
            calendarId='primary', timeMin=time_min, timeMax=time_max,
            singleEvents=True, orderBy='startTime').execute().get('items', [])
            
        for ge in g_events:
            start_str = ge['start'].get('dateTime')
            end_str = ge['end'].get('dateTime')
            if start_str and end_str:
                # המרת התאריך של גוגל לפייתון רגיל
                start_dt = datetime.fromisoformat(start_str.replace('Z', '+00:00')).replace(tzinfo=None)
                end_dt = datetime.fromisoformat(end_str.replace('Z', '+00:00')).replace(tzinfo=None)
                loc = ge.get('location', 'Anywhere')
                fixed_events.append({"start": start_dt, "end": end_dt, "location": loc})

    # 2. חישוב חללים לבנים מודע-מיקום
    free_blocks = []
    current_time = start_of_day
    current_location = "חובבי ציון 37 תל אביב" 

    for event in fixed_events:
        block_end = event["start"] - timedelta(minutes=15)
        if current_time < block_end:
            free_blocks.append({"start": current_time, "end": block_end, "location": current_location})
        current_time = max(current_time, event["end"])
        current_location = event["location"]

    if current_time < end_of_day:
        free_blocks.append({"start": current_time, "end": end_of_day, "location": current_location})

    # 3. שיבוץ המשימות הפתוחות
    pending_tasks = session.exec(select(Task).where(Task.status == "new").order_by(Task.priority)).all()
    scheduled_count = 0

    for task in pending_tasks:
        task_duration = timedelta(minutes=task.duration_minutes)
        for block in free_blocks:
            transit_mins = 0
            if task.location_context != "Anywhere" and block["location"] != "Anywhere" and task.location_context != block["location"]:
                transit_mins = task.estimated_transit_minutes
                
            transit_time = timedelta(minutes=transit_mins)
            total_required = task_duration + transit_time
            block_duration = block["end"] - block["start"]
            
            if block_duration >= total_required:
                task.start_time = block["start"] + transit_time
                task.end_time = task.start_time + task_duration
                task.status = "scheduled"
                session.add(task)
                scheduled_count += 1
                
                block["start"] = task.end_time
                if task.location_context != "Anywhere":
                    block["location"] = task.location_context
                break 

    session.commit()
    return {"message": f"שובצו {scheduled_count} משימות בהצלחה!"}

@app.post("/sync_to_google")
def sync_to_google(session: Session = Depends(get_session)):
    """לוקח את כל המשימות ששובצו ויורה אותן ליומן גוגל האמיתי"""
    if not os.path.exists('token.json'):
        return {"error": "לא מחובר לגוגל. התחבר קודם."}
        
    creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    service = build('calendar', 'v3', credentials=creds)

    tasks_to_sync = session.exec(select(Task).where(Task.status == "scheduled")).all()
    synced_count = 0
    
    for task in tasks_to_sync:
        event = {
            'summary': f"🤖 {task.title}",
            'location': task.location_context if task.location_context != 'Anywhere' else '',
            'description': f"שובץ על ידי מנהל הזמן החכם.\nזמן נסיעה משוער: {task.estimated_transit_minutes} דקות.",
            'start': {'dateTime': task.start_time.isoformat(), 'timeZone': 'Asia/Jerusalem'},
            'end': {'dateTime': task.end_time.isoformat(), 'timeZone': 'Asia/Jerusalem'},
        }
        
        service.events().insert(calendarId='primary', body=event).execute()
        
        # מעדכנים סטטוס כדי שלא ישובץ שוב פעמיים
        task.status = "synced"
        session.add(task)
        synced_count += 1
        
    session.commit()
    return {"message": f"סונכרנו {synced_count} משימות ליומן גוגל בהצלחה!"}