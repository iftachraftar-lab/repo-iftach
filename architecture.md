# NutriAgent AI — מפרט ארכיטקטורת מערכת (System Architecture)

> **גרסה פעילה:** v63 (Cloud Run Production)  
> **תאריך עדכון:** נובמבר 2026  
> **סביבת ענן:** Google Cloud Platform (`me-west1` Tel Aviv)

---

## 1. מבט-על ארכיטקטוני (System High-Level Architecture)

המערכת בנויה כ-Modern Micro-Services Architecture מונחית אירועים, המשלבת ממשק משתמש קצה מתקדם (PWA), שכבת API מאובטחת בענן ב-FastAPI, ומנוע בינה מלאכותית רב-מודאלי (Gemini AI Vision & NLP).

```mermaid
flowchart TD
    subgraph Client ["Client Layer (Mobile / Desktop PWA)"]
        UI["Alpine.js Reactive State"]
        SW["Service Worker (PWA & Offline Cache)"]
        Sensors["WebRTC Camera / Mic / Barcode Scanner"]
    end

    subgraph Edge ["Edge & Ingress (Google Cloud)"]
        CDN["Cloud Run HTTPS Load Balancer"]
        SSL["Automated SSL & Security Headers"]
    end

    subgraph Backend ["Application Layer (FastAPI on Cloud Run)"]
        API["FastAPI Async Engine (bot_server.py)"]
        Auth["Google OAuth 2.0 & RBAC Gatekeeper"]
        RateLimit["Sliding-Window Rate Limiter"]
        BusinessLogic["Nutrition & Fitness Calculation Engine"]
    end

    subgraph AI_Engine ["Multimodal AI & External APIs"]
        GeminiVision["Gemini Vision (Dish & Label OCR)"]
        GeminiNLP["Gemini 2.5 (Clinical Coach & Grocery AI)"]
        BarcodeDB["Open Food Facts & Israeli Barcode DB"]
        WoltEngine["Wolt Balance Scraper & Recommender"]
    end

    subgraph Data ["Persistence Layer"]
        Firestore["Google Cloud Firestore (Multi-Tenant NoSQL)"]
        MemCache["Local In-Memory / Dev Cache"]
    end

    UI <--> SW
    SW <--> CDN
    CDN <--> API
    API <--> Auth
    API <--> RateLimit
    API <--> BusinessLogic
    BusinessLogic <--> GeminiVision
    BusinessLogic <--> GeminiNLP
    BusinessLogic <--> BarcodeDB
    BusinessLogic <--> WoltEngine
    BusinessLogic <--> Firestore
```

---

## 2. שכבות המערכת (Detailed Layer Breakdown)

### 2.1 שכבת הממשק (Frontend / Client Layer)
- **ארכיטקטורה:** Single-Page Progressive Web App (PWA) רספונסיבי, מותאם Mobile-First (iOS Safari & Android Chrome).
- **ניהול מצב (State Management):** ריאקטיביות קלת-משקל ומבוזרת באמצעות **Alpine.js** (`nutritionApp()`), ללא צורך ב-Node.js Build צד-לקוח, המאפשר טעינה מיידית (<0.5 שניות).
- **סגנון ומערכת עיצוב:** Tailwind CSS עם פלטת צבעים מותאמת (Clinical Duotone: Emerald, Indigo, Slate-900), וספריית סמלים וקטוריים ייעודית (SVG Sprite של 24 אייקונים דואוטוניים).
- **PWA & Offline:** `sw.js` מיישם אסטרטגיית **Network-First** לקבלת עדכונים חיים מיידיים, עם Offline Fallback מלא וסנכרון ברקע.
- **מנוע ריבוי שפות (i18n):** תמיכה מלאה ואינטגרלית בעברית (RTL) ובאנגלית (LTR) ברמת הממשק, כפתורים, תאריכים והודעות מאמן.

```mermaid
flowchart LR
    A["משתמש (טקסט / קול / תמונה / ברקוד)"] --> B["Camera / Mic / File API"]
    B --> C["Client-side Pre-Processing"]
    C --> D["FastAPI REST Endpoint"]
```

---

### 2.2 שכבת ה-API והשרת (Backend / Application Layer)
- **טכנולוגיה:** Python 3.12 עם **FastAPI** ו-Uvicorn (Asynchronous ASGI).
- **אבטחה והרשאות:**
  - **Google Identity Services (OAuth 2.0):** אימות טוקן JWT ישירות מול שרתי Google OAuth.
  - **שער אישור מנהל (RBAC Admin Gate):** משתמש חדש נוצר במצב `is_approved: false`. מנהל מערכת (`is_admin: true`) מאשר או חוסם משתמשים דרך דשבורד הניהול המאובטח.
  - **Rate Limiting:** הגבלת קצב דינמית למניעת ניצול יתר או מתקפות על מנועי ה-AI.
  - **CORS & Security Headers:** Content-Security-Policy קפדני, מניעת XSS, ומדיניות עוגיות מאובטחת.
- **חישובים מטאבוליים מבוססי מדע:**
  - אלגוריתם **Mifflin-St Jeor** לקביעת BMR ו-TDEE.
  - חלוקת מאקרו מותאמת אישית: יחס חלבון (1.6g–2.2g לק"ג משקל גוף), פחמימות ושומנים לפי יעד (חיטוב, מסה, שמירה).

---

### 2.3 מנוע הבינה המלאכותית (Multimodal AI Engine)

המערכת משתמשת במודלי ה-AI המתקדמים בעולם מבית Google DeepMind:

| מודול | מודל / מנוע | תפקיד במערכת |
| :--- | :--- | :--- |
| **זיהוי מנות וצילום צלחת** | `Gemini Vision` | פירוק הצלחת למרכיבים, הערכת משקלים קלינית, חישוב קלוריות, חלבון, פחמימות ושומן. |
| **סינון אובייקטים שאינם מזון** | Guardrail Detection | **דחייה על הסף** של פריטים שאינם מזון (למשל רהיטים, קמינים, מוצרי חשמל) עם אופציית עקיפה ידנית בלבד. |
| **סורק סופר ("האם לקנות?")** | Vision + OpenFoodFacts | ניתוח תמונת מוצר או תווית ערכים בסופר ומתן ציון התאמה (0-100) ליתרה הקלורית היומית. |
| **מאמן תזונה ואימונים AI** | `Gemini 2.5 Flash` | ניתוח בזמן אמת של חלון הצום, עמידה ביעד החלבון, והמלצה על ארוחה עתידית לסגירת היום. |
| **רשימת קניות חכמה לסופר** | Gemini Structured Output | סריקת החוסרים התזונתיים של השבוע והפקת רשימת קניות ממוינת לפי מחלקות סופר עם שיתוף לוואטסאפ. |
| **התאמת מנות מ-Wolt** | Wolt Semantic Fitting | סינון מנות ממסעדות באזור שמתאימות במדויק לחלון הקלורי שנותר בתקציב. |

---

## 3. תרשים זרימת נתונים: ניתוח ארוחה בצילום (Photo Logging Workflow)

```mermaid
sequenceDiagram
    autonumber
    actor User as משתמש בנייד
    participant UI as ממשק PWA (Alpine.js)
    participant Server as שרת FastAPI
    participant Gemini as Gemini Multimodal Vision
    participant DB as מסד נתונים Firestore

    User->>UI: לוחץ "צלם ארוחה" ומצלם את הצלחת
    UI->>UI: כיווץ תמונה מקומי ב-Canvas (Max 1280px)
    UI->>Server: POST /api/meal/photo (Base64 JPEG)
    Server->>Server: אימות הרשאות משתמש ומכסת שימוש
    Server->>Gemini: הנחיית ניתוח קלינית מובנית (JSON Schema)
    Gemini-->>Server: פלט מובנה (מרכיבים, קלוריות, חלבון, רמת ביטחון)
    
    alt האובייקט אינו מזון (is_food == false)
        Server-->>UI: דחייה עם סיבה + כפתור הזנה ידנית
    else זיהוי מזון תקין
        Server->>DB: שמירת הארוחה, שעת התיעוד ונתוני המאקרו
        Server-->>UI: אישור + עדכון מיידי של טבעות הקלוריות והחלבון
        UI->>User: הצגת הארוחה ביומן והמלצת מאמן מעודכנת
    end
```

---

## 4. מודל הנתונים (Core Data Entities)

המערכת פועלת בארכיטקטורת **Multi-Tenant** מופרדת לחלוטין:

```mermaid
erDiagram
    USER ||--o{ MEAL : logs
    USER ||--o{ WORKOUT : performs
    USER ||--o{ VISION_LOG : captures
    USER ||--o{ ACHIEVEMENT : unlocks
    USER ||--o{ GROCERY_ITEM : buys

    USER {
        string id PK
        string email
        string name
        boolean is_approved
        boolean is_admin
        int target_calories
        float target_protein
        float target_carbs
        float target_fats
        int current_streak
    }

    MEAL {
        string id PK
        string user_id FK
        string description
        int calories
        float protein_g
        float carbs_g
        float fats_g
        string log_time
        string source
        string thumbnail_data_url
    }

    WORKOUT {
        string id PK
        string user_id FK
        string activity_name
        int burned_calories
        int duration_min
        string log_time
    }

    ACHIEVEMENT {
        string id PK
        string user_id FK
        string badge_name
        string unlocked_date
    }
```

---

## 5. תשתית הענן ו-DevOps (Infrastructure & Deployment)

המערכת פרוסה באופן מלא על גבי שירותי **Google Cloud Platform (GCP)** באזור `me-west1` (תל אביב):

- **Cloud Run (Serverless Container):**
  - קונטיינר מבוסס Dockerfile אופטימלי (Alpine / Slim Python).
  - Scale-to-Zero אוטומטי (חיסכון מרבי בעלויות) עם זינוק מיידי עד עשרות Instances במקרה של עומס.
  - זיכרון: 1GB RAM, 1 vCPU, תמיכה מלאה ב-Concurrency של 80 בקשות בו-זמנית לכל מופע.
- **Cloud Build & Git CI/CD:**
  - פריסה אוטומטית ישירות ממאגר ה-Git באמצעות `gcloud run deploy`.
  - Service URL חי עם SSL מלא: `https://nutrition-service-313955139672.me-west1.run.app`.
- **Google Cloud Secret Manager & IAM:**
  - ניהול מאובטח של מפתחות ה-API של Gemini ואישורי OAuth ללא שמירת סודות בקוד.
- **Cloud Monitoring & Logging:**
  - איסוף לוגים וצילומי Telemetry בזמן אמת לאיתור חריגות וזמני תגובה.

---

## 6. סיכום יתרונות הארכיטקטורה (Architectural Highlights)
1. **אפס תלויות שבירות:** ה-Frontend לא דורש Webpack/Vite מורכב — הקוד רץ ישירות מכל דפדפן במהירות שיא.
2. **הגנה מוחלטת מהזיות AI:** מנגנון Rejection Guardrail מונע מקרים שבהם חפצים ביתיים מוזנים בטעות כמזון.
3. **שמירה על פרטיות:** הפרדה קפדנית בין משתמשים (User ID Isolation) ללא חשיפת נתוני תזונה בין חשבונות.
4. **עומס קוגניטיבי מינימלי (Zen UX):** ארגון הכלים המתקדמים בטאבים ייעודיים, כפתורי שטח מגע אחידים וממשק מובייל מהיר.

