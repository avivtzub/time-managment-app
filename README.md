<div align="center">

# 🤖 AI Smart Time Manager

*An intelligent, NLP-powered scheduling assistant that translates your natural language into an optimized Google Calendar.*

[![Python](https://img.shields.io/badge/Python-3.8+-blue.svg?style=for-the-badge&logo=python&logoColor=white)](#)
[![FastAPI](https://img.shields.io/badge/FastAPI-005571?style=for-the-badge&logo=fastapi)](#)
[![Google Calendar](https://img.shields.io/badge/Google_Calendar-API-4285F4?style=for-the-badge&logo=googlecalendar&logoColor=white)](#)
[![Gemini AI](https://img.shields.io/badge/Gemini_AI-Flash_2.5-8E75B2?style=for-the-badge&logo=google&logoColor=white)](#)
[![Tailwind CSS](https://img.shields.io/badge/Tailwind_CSS-38B2AC?style=for-the-badge&logo=tailwind-css&logoColor=white)](#)

</div>

<br />

## 📖 Overview

**AI Smart Time Manager** takes the friction out of daily planning. Instead of manually searching for free slots and creating calendar blocks, simply tell the AI what you need to do (e.g., *"I need to study algorithms for two hours on Thursday morning at the library"*). 

The system will automatically scan your Google Calendar (including external schedules like university classes), find the perfect available slot, calculate estimated transit times, and sync it directly to your cloud calendar.

## ✨ Key Features

* **🧠 Natural Language Processing:** Powered by Google's Gemini AI to extract task duration, preferred time of day, priority, and physical location from free-text inputs.
* **🗓️ Smart Scheduling Algorithm:** Automatically finds available slots without double-booking over existing hard events.
* **🚗 Dynamic Transit Calculation:** Estimates and allocates buffer times for travel if a task requires a specific location.
* **🖱️ Interactive UI:** Visually review proposed slots, Drag & Drop to adjust times, and manually edit events before pushing them to the cloud.
* **☁️ Cloud Sync:** One-click two-way synchronization with Google Calendar via secure OAuth 2.0.
* **⚡ Priority Engine:** Intelligently schedules urgent tasks for "today" and defers flexible tasks to future slots.

## 📸 Screenshots

*(Add your screenshots here!)*
> **Pro Tip:** Take a screenshot of the main UI and a screenshot of the calendar after the AI proposes a schedule.
> 
> `![Main UI](link_to_image_1.png)`
> `![Calendar View](link_to_image_2.png)`

## 💻 Tech Stack

### Backend
* **Python 3**
* **FastAPI** (with Uvicorn) for high-performance API routing.
* **SQLModel / SQLite** for local database and ORM.

### Frontend
* **Vanilla JavaScript & HTML** for lightweight client-side logic.
* **Tailwind CSS** for modern, responsive styling.
* **FullCalendar.js** for interactive calendar rendering and event manipulation.

### Integrations & APIs
* **Google Calendar API (v3)**
* **Google Gemini API (`gemini-2.5-flash`)**

## 🚀 Quick Start

### Prerequisites
* Python 3.8+
* A Google Cloud project with the **Google Calendar API** enabled.
* OAuth 2.0 Client credentials (`credentials.json`) from Google Cloud.
* A Gemini API Key from Google AI Studio.

### Installation & Running

1. **Clone the repository:**
   ```bash
   git clone https://github.com/avivtzub/ai-time-manager.git
   cd ai-time-manager
   ```

2. **Create and activate a virtual environment:**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows use: venv\Scripts\activate
   ```

3. **Install dependencies:**
   ```bash
   pip install fastapi uvicorn sqlmodel google-generativeai google-auth-oauthlib google-api-python-client python-dotenv
   ```

4. **Environment Variables:**
   Create a `.env` file in the root directory and add your Gemini API key:
   ```env
   GEMINI_API_KEY=your_api_key_here
   ```

5. **Start the server:**
   ```bash
   uvicorn main:app --reload
   ```

6. Open your browser and navigate to `http://localhost:8000/`.
7. Click **"Connect with Google Calendar"** to authorize the application.

## 🏗️ Architecture & Data Flow

1. **Input:** User submits a free-text task via the UI.
2. **AI Parsing:** FastAPI sends the prompt to Gemini AI, which returns a structured JSON (duration, date, preference, priority, transit time).
3. **Database:** Task is saved locally in SQLite (`status: new`).
4. **Algorithm:** The Python backend fetches existing Google Calendar events (including external ones), calculates free blocks, applies time constraints/travel buffers, and updates the task (`status: scheduled`).
5. **Review:** The frontend displays the proposed schedule in green. The user can drag, stretch, or edit the block.
6. **Sync:** Upon confirmation, the backend pushes the finalized event to Google Calendar and marks it as `synced`.

## 🔒 Security Note
This application uses local configuration files for API keys and tokens. Ensure that your `.env`, `credentials.json`, `token.json`, and `tasks.db` are added to your `.gitignore` file before pushing to a public repository.

---
<div align="center">
  <b>Developed by Aviv</b><br>
  <a href="[https://github.com/avivtzub](https://github.com/avivtzub)">GitHub Profile</a>
</div>
