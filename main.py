from fastapi import FastAPI, Depends, File, UploadFile
import icalendar
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from sqlmodel import Field, Session, SQLModel, create_engine, select
from typing import Optional, List
from datetime import datetime, timedelta
import google.generativeai as genai
import json
from pydantic import BaseModel

# 1. הגדרת המפתח של ה-AI
import os

# המפתח יימשך ממשתני הסביבה של השרת
gemini_api_key = os.environ.get("GEMINI_API_KEY") 
if not gemini_api_key:
    raise ValueError("No GEMINI_API_KEY set for application")

genai.configure(api_key=gemini_api_key)
# 2. הגדרת מסד הנתונים
sqlite_file_name = "tasks.db"
sqlite_url = f"sqlite:///{sqlite_file_name}"
engine = create_engine(sqlite_url, connect_args={"check_same_thread": False})

# --- החלף את המחלקה הקיימת בזו (הוספנו priority ו-requires_travel) ---
# --- החלף את המחלקה הקיימת בזו ---
class Task(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    title: str
    energy_level: int
    can_be_parallel: bool = False
    is_container: bool = False
    deadline: Optional[datetime] = None
    status: str = "new"
    parent_id: Optional[int] = Field(default=None)
    is_fixed_event: bool = False          
    start_time: Optional[datetime] = None 
    end_time: Optional[datetime] = None   
    duration_minutes: Optional[int] = 30  
    priority: int = 2          
    requires_travel: bool = False
    
    # השדות החדשים לניהול מיקומים:
    location_context: str = "Anywhere" 
    estimated_transit_minutes: int = 0 # הערכת ה-AI לזמן הנסיעה למיקום הזה
# 3. יצירת השרת
app = FastAPI(title="Aviv's Smart Time Manager")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"], 
    allow_headers=["*"],
)

@app.on_event("startup")
def on_startup():
    SQLModel.metadata.create_all(engine)

def get_session():
    with Session(engine) as session:
        yield session

# ---------------------------------------------------------
# Endpoints (דלתות ה-API)
# ---------------------------------------------------------

@app.post("/add_task", response_model=Task)
def add_task(task: Task, session: Session = Depends(get_session)):
    session.add(task)
    session.commit()
    session.refresh(task)
    return task

@app.get("/tasks", response_model=List[Task])
def get_tasks(session: Session = Depends(get_session)):
    tasks = session.exec(select(Task)).all()
    return tasks

class SmartTaskRequest(BaseModel):
    text: str

# --- החלף את הפונקציה הקיימת בזו ---
@app.post("/smart_add_task")
def smart_add_task(request: SmartTaskRequest, session: Session = Depends(get_session)):
    prompt = f"""
    אתה מנהל זמן אישי חכם. 
    המשתמש יזרוק לך משפט חופשי. עליך לחלץ את המשימה ולהחזיר *רק* מבנה JSON תקין.
    
    חוקי מיקומים (קריטי!):
    המשתמש משתמש בשמות קוד. עליך לתרגם אותם לכתובות המלאות בשדה location_context:
    - המילה "אוניברסיטה" או "קמפוס" -> "אוניברסיטת תל אביב"
    - המילה "דירה" -> "חובבי ציון 37 תל אביב"
    - המילה "בית" או "הורים" -> "בילו 39 רעננה"
    אם אין מיקום ספציפי שמשתמע מהבקשה, ה-location_context יהיה "Anywhere".

    חוקים נוספים:
    - energy_level: 1 (ריכוז), 2 (בינוני), 3 (קליל).
    - duration_minutes: הערכת זמן ביצוע בדקות.
    - priority: 1 (דחוף), 2 (רגיל), 3 (פנאי).
    - requires_travel: true אם המשימה מצריכה יציאה למקום ספציפי שאינו Anywhere.
    - estimated_transit_minutes: הערך כמה דקות נסיעה ממוצעת לוקח להגיע למיקום הזה ביום יום (אם זה Anywhere, שים 0).
    - is_fixed_event: false.
    
    החזר בדיוק את המבנה הזה:
    {{
        "title": "שם המשימה",
        "energy_level": 1,
        "duration_minutes": 60,
        "priority": 2,
        "requires_travel": true,
        "location_context": "אוניברסיטת תל אביב",
        "estimated_transit_minutes": 45,
        "is_fixed_event": false
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
        session.refresh(new_task)
        
        return {"message": "ה-AI פענח את המשימה בהצלחה!", "task": new_task}
    except Exception as e:
        return {"error": "הייתה בעיה בפענוח המשימה", "details": str(e)}

@app.get("/export_calendar")
def export_calendar(session: Session = Depends(get_session)):
    """מייצר קובץ יומן (.ics) שניתן להוריד ישירות לאייפון"""
    tasks = session.exec(select(Task)).all()
    
    ics_content = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Aviv Smart Time Manager//IL"
    ]
    
    base_time = datetime.now() + timedelta(days=1)
    base_time = base_time.replace(hour=9, minute=0, second=0, microsecond=0)
    
    for task in tasks:
        # עכשיו אנחנו מוסיפים ליומן רק משימות ששובצו, ומשתמשים בזמן האמיתי שלהן!
        if task.status == "scheduled" and task.start_time and task.end_time:
            dtstart = task.start_time.strftime("%Y%m%dT%H%M%S")
            dtend = task.end_time.strftime("%Y%m%dT%H%M%S")
            
            ics_content.extend([
                "BEGIN:VEVENT",
                f"SUMMARY:{task.title}",
                f"DTSTART:{dtstart}",
                f"DTEND:{dtend}",
                f"DESCRIPTION:Energy Level: {task.energy_level}",
                "END:VEVENT"
            ])
        
    ics_content.append("END:VCALENDAR")
    ics_string = "\n".join(ics_content)
    
    return Response(content=ics_string, media_type="text/calendar", headers={
        "Content-Disposition": "attachment; filename=aviv_schedule.ics"
    })
@app.post("/import_calendar")
@app.post("/import_calendar")
async def import_calendar(file: UploadFile = File(...), session: Session = Depends(get_session)):
    """
    גרסה משוריינת: מנקה תווים נסתרים ומטפלת באירועים של יום שלם
    """
    try:
        content = await file.read()
        
        # 1. ניקוי הקובץ מתווים נסתרים (הורגים את ה-\r הבעייתי)
        clean_content = content.replace(b'\r\n', b'\n').replace(b'\r', b'\n')
        cal = icalendar.Calendar.from_ical(clean_content)
        
        added_events = 0
        from datetime import datetime # יבוא נקודתי למקרה שנצטרך להמיר תאריכים
        
        for component in cal.walk():
            if component.name == "VEVENT":
                summary = str(component.get('summary', 'אירוע ללא שם'))
                
                # משיכת אובייקטי הזמן
                start_prop = component.get('dtstart')
                end_prop = component.get('dtend')
                
                if not start_prop or not end_prop:
                    continue # מדלגים אם חסר תאריך התחלה או סיום
                    
                start_dt = start_prop.dt
                end_dt = end_prop.dt
                
                # 2. רשת ביטחון לאירועים של יום שלם (date במקום datetime)
                if not hasattr(start_dt, 'hour'):
                    start_dt = datetime.combine(start_dt, datetime.min.time())
                    end_dt = datetime.combine(end_dt, datetime.min.time())
                
                # 3. מחיקת אזור הזמן כדי ש-SQLite לא יקרוס
                if hasattr(start_dt, 'replace') and start_dt.tzinfo:
                    start_dt = start_dt.replace(tzinfo=None)
                if hasattr(end_dt, 'replace') and end_dt.tzinfo:
                    end_dt = end_dt.replace(tzinfo=None)
                    
                new_event = Task(
                    title=summary,
                    energy_level=0,
                    is_fixed_event=True, 
                    start_time=start_dt,
                    end_time=end_dt,
                    status="scheduled"
                )
                session.add(new_event)
                added_events += 1
                
        session.commit()
        return {"message": f"הצלחה! יובאו {added_events} אירועים קבועים ללוז שלך."}
        
    except Exception as e:
        return {"error": "הייתה בעיה בקריאת קובץ היומן", "details": str(e)}
@app.post("/schedule_tasks")
@app.post("/schedule_tasks")
def schedule_tasks(session: Session = Depends(get_session)):
    """
    האלגוריתם הסופי: מזהה מיקומים, מונע דו"צים ומחשב זמני מעבר דינמיים!
    """
    # 0. איפוס הלוח
    previously_scheduled = session.exec(
        select(Task).where(Task.is_fixed_event == False, Task.status == "scheduled")
    ).all()
    for t in previously_scheduled:
        t.status = "new"
        t.start_time = None
        t.end_time = None
        session.add(t)
    session.commit()

    # 1. חלון הזמן למחר
    tomorrow = datetime.now() + timedelta(days=1)
    start_of_day = tomorrow.replace(hour=8, minute=0, second=0, microsecond=0)
    end_of_day = tomorrow.replace(hour=22, minute=0, second=0, microsecond=0)

    # 2. אירועים קשיחים
    fixed_events = session.exec(
        select(Task).where(
            Task.is_fixed_event == True,
            Task.start_time >= start_of_day,
            Task.start_time <= end_of_day
        ).order_by(Task.start_time)
    ).all()

    # 3. הכנת החללים הלבנים (הפעם עם מעקב מיקום!)
    free_blocks = []
    current_time = start_of_day
    
    # נקודת המוצא של תחילת היום
    current_location = "חובבי ציון 37 תל אביב" 

    for event in fixed_events:
        # נשאיר באפר קטן של 15 דקות לפני הרצאות קשיחות לביטחון
        block_end = event.start_time - timedelta(minutes=15)
        
        if current_time < block_end:
            free_blocks.append({
                "start": current_time, 
                "end": block_end,
                "current_location": current_location # מאיפה אנחנו מתחילים את החלל הזה
            })
        
        current_time = max(current_time, event.end_time)
        # עדכון המיקום למיקום של האירוע שהסתיים (אם אין, נניח Anywhere)
        current_location = event.location_context if event.location_context else "Anywhere"

    if current_time < end_of_day:
        free_blocks.append({
            "start": current_time, 
            "end": end_of_day,
            "current_location": current_location
        })

    # 4. משימות פתוחות, ממוינות לפי עדיפות (Priority)
    pending_tasks = session.exec(
        select(Task).where(
            Task.is_fixed_event == False,
            Task.status == "new"
        ).order_by(Task.priority) 
    ).all()

    scheduled_count = 0

    # 5. שיבוץ מודע-מיקום (Location-Aware)
    for task in pending_tasks:
        task_duration = timedelta(minutes=task.duration_minutes)
        
        for block in free_blocks:
            # חישוב זמן מעבר דינמי: רק אם המיקומים שונים ואף אחד מהם לא "בכל מקום"
            transit_mins = 0
            if task.location_context != "Anywhere" and block["current_location"] != "Anywhere" and task.location_context != block["current_location"]:
                transit_mins = task.estimated_transit_minutes
                
            transit_time = timedelta(minutes=transit_mins)
            total_required_time = task_duration + transit_time
            
            block_duration = block["end"] - block["start"]
            
            # האם המשימה + זמן הנסיעה נכנסים בחלל?
            if block_duration >= total_required_time:
                # משבצים! קודם הנסיעה, ואז המשימה מתחילה.
                task.start_time = block["start"] + transit_time
                task.end_time = task.start_time + task_duration
                task.status = "scheduled"
                
                session.add(task)
                scheduled_count += 1
                
                # מעדכנים את החלל הלבן לטובת המשימה הבאה שתרצה להיכנס פה
                block["start"] = task.end_time
                # מעדכנים את המיקום שלנו בחלל הזה למקום שבו סיימנו הרגע את המשימה!
                if task.location_context != "Anywhere":
                    block["current_location"] = task.location_context
                
                break # עוברים למשימה הבאה בתור

    session.commit()
    
    return {
        "message": f"הלוח אופס! שובצו {scheduled_count} משימות כולל זמני מעבר מדויקים.",
        "free_blocks_found": len(free_blocks)
    }