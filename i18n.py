"""
classctl.i18n  —  interface language, and the layout mirroring that Hebrew needs.

tkinter has no right-to-left mode, so the layout is mirrored by hand: anchors,
justification and pack sides all flow through the helpers here. Call side(),
anchor() and justify() instead of writing "left"/"w"/"left" directly, and the
same code lays out correctly in both languages.
"""

_LANG = "en"

HEBREW = {
    # --- console, main screen ---
    "ClassCtl": "ClassCtl",
    "Classroom operations console": "מערכת שליטה על מחשבי כיתה",
    "GROUP": "קבוצה",
    "STATIONS": "עמדות",
    "ACTIONS": "פעולות",
    "Rescan": "סריקה מחדש",
    "Test": "בדיקה",
    "Manage scripts": "ניהול סקריפטים",
    "Open an app": "פתיחת תוכנה",
    "Sign in": "כניסה",
    "Show password": "הצג סיסמה",
    "Wrong password": "סיסמה שגויה",
    "That password does not match. Try again.": "הסיסמה אינה נכונה. נסה שוב.",
    "{n} online": "{n} מחוברות",
    "scanning…": "סורק…",
    "testing…": "בודק…",
    "deep scan…": "סריקה מעמיקה…",
    "deep scan {pct}%": "סריקה מעמיקה {pct}%",

    # --- empty state ---
    "This PC is named '{host}', which has no group prefix. Rename it as "
    "<group>-<number>, for example LAB1-12 or LAB1-INS, then restart.":
        "שם המחשב הוא '{host}', ואין בו קידומת קבוצה. שנה אותו לתבנית "
        "<קבוצה>-<מספר>, למשל LAB1-12 או LAB1-INS, ואז הפעל מחדש.",
    "No other stations answered, by broadcast or by scanning this subnet. "
    "Check that the agent runs on them, that their names start with the same "
    "prefix, and that TCP 48720 / UDP 48719 are allowed. If the stations sit "
    "on another subnet, run diagnose.py --peer <ip> from here.":
        "אף עמדה אחרת לא ענתה, לא בשידור ולא בסריקת הרשת. ודא שהסוכן רץ עליהן, "
        "שהשמות שלהן מתחילים באותה קידומת, ושהפורטים TCP 48720 ו-UDP 48719 "
        "פתוחים. אם העמדות ברשת אחרת, הרץ מכאן diagnose.py --peer <כתובת>.",

    # --- confirm ---
    "stations will run this": "עמדות יבצעו את הפעולה",
    "station will run this": "עמדה אחת תבצע את הפעולה",
    "Choose stations": "בחירת עמדות",
    "Hide stations": "הסתר עמדות",
    "Tap a station to leave it out of this action.":
        "לחץ על עמדה כדי להשאיר אותה מחוץ לפעולה.",
    "Run now": "בצע עכשיו",
    "Cancel": "ביטול",
    "No stations": "אין עמדות",
    "No stations answered the last scan. Press Rescan first.":
        "אף עמדה לא ענתה בסריקה האחרונה. לחץ על סריקה מחדש.",
    "Nothing selected": "לא נבחר דבר",
    "Every station is set to be skipped.": "כל העמדות מסומנות לדילוג.",

    # --- sending / results ---
    "Sending to {n} stations…": "שולח ל-{n} עמדות…",
    "Stations that are switched off take a few seconds.":
        "עמדות כבויות לוקחות כמה שניות.",
    "All {n} stations done": "כל {n} העמדות הצליחו",
    "{ok} of {total} succeeded": "{ok} מתוך {total} הצליחו",
    "DID NOT RESPOND": "לא הגיבו",
    "Close": "סגור",
    "Retry {n}": "נסה שוב {n}",
    "Run on this PC": "הפעל על מחשב זה",
    "Run here too": "להפעיל גם כאן",
    "Run '{name}' on this PC now?": "להפעיל '{name}' על המחשב הזה עכשיו?",
    "Run here": "הפעל כאן",
    "Script missing": "הסקריפט חסר",
    "'{name}' is not in the scripts folder.": "'{name}' לא נמצא בתיקיית הסקריפטים.",
    "Could not run script": "לא ניתן להריץ את הסקריפט",
    "Could not read script": "לא ניתן לקרוא את הסקריפט",
    "Station test": "בדיקת עמדות",

    # --- scripts manager ---
    "SCRIPTS": "סקריפטים",
    "Each file here is one action button.": "כל קובץ כאן הוא כפתור פעולה אחד.",
    "Add script": "הוסף סקריפט",
    "Add an app": "הוסף תוכנה",
    "Open an application": "פתיחת תוכנה",
    "Pick an application and it is saved as a script, so it becomes a button "
    "like the rest.":
        "בחר תוכנה והיא תישמר כסקריפט, כך שתהפוך לכפתור כמו כל השאר.",
    "or type a command or full path": "או הקלד פקודה או נתיב מלא",
    "An app can become an action too: pick one and it is saved here as a script.":
        "גם תוכנה יכולה להפוך לפעולה: בחר אחת והיא תישמר כאן כסקריפט.",
    "Action added": "הפעולה נוספה",
    "'{name}' is now a button. Press it to open the app on every station.":
        "'{name}' הוא כפתור עכשיו. לחיצה עליו תפתח את התוכנה בכל העמדות.",
    "Edit": "עריכה",
    "Delete": "מחיקה",
    "Pick a script": "בחר סקריפט",
    "Select a script from the list first.": "בחר קודם סקריפט מהרשימה.",
    "Delete script": "מחיקת סקריפט",
    "Delete '{name}'?\nIts action button disappears.":
        "למחוק את '{name}'?\nכפתור הפעולה שלו ייעלם.",
    "Could not add script": "לא ניתן להוסיף את הסקריפט",
    "Could not delete script": "לא ניתן למחוק את הסקריפט",
    "Could not open editor": "לא ניתן לפתוח את העורך",
    "No actions yet.\nAdd a script file and it becomes a button.":
        "אין עדיין פעולות.\nהוסף קובץ סקריפט והוא יהפוך לכפתור.",

    # --- open an app ---
    "Open an app on every station": "פתיחת תוכנה בכל העמדות",
    "PICK AN APP": "בחר תוכנה",
    "OR TYPE A COMMAND / PATH": "או הקלד פקודה / נתיב",
    "Browse": "עיון",
    "It opens on the station screens, not here.":
        "התוכנה תיפתח על מסכי העמדות, לא כאן.",
    "Open on all": "פתח בכולן",
    "Pick an app": "בחר תוכנה",
    "Choose one from the list, or type a command.":
        "בחר אחת מהרשימה, או הקלד פקודה.",
    "Open {name}": "פתיחת {name}",
    "Open {app}": "פתיחת {app}",
    "Choose a program": "בחר תוכנה",

    # --- installer, launcher ---
    "CLASSCTL SETUP": "התקנת CLASSCTL",
    "Already installed on this computer.": "כבר מותקן על המחשב הזה.",
    "Not installed on this computer yet.": "עדיין לא מותקן על המחשב הזה.",
    "Install": "התקנה",
    "Set a password, choose the folder and the starter actions.":
        "קביעת סיסמה, בחירת תיקייה ופעולות התחלתיות.",
    "Add actions": "הוספת פעולות",
    "Add": "הוסף",
    "Add starter actions you did not pick during setup.":
        "הוספת פעולות התחלתיות שלא בחרת בהתקנה.",
    "Uninstall": "הסרה",
    "Stop the agent, close the two ports, remove the shortcuts and delete the "
    "protected folder.":
        "עצירת הסוכן, סגירת שני הפורטים, הסרת הקיצורים ומחיקת התיקייה המוגנת.",
    "Language": "שפה",
    "Update": "עדכון",
    "Check the public repository and install the newest version. Your password, "
    "key and scripts are kept.":
        "בודק בריפוזיטורי הציבורי ומתקין את הגרסה החדשה. הסיסמה, המפתח "
        "והסקריפטים שלך נשמרים.",
    "Update ClassCtl": "עדכון ClassCtl",
    "Fetches the newest version from the public repository.":
        "מוריד את הגרסה החדשה מהריפוזיטורי הציבורי.",
    "Installed version: {v}": "הגרסה המותקנת: {v}",
    "Checking the repository\u2026": "בודק בריפוזיטורי…",
    "Could not reach the repository. Check the internet connection.":
        "לא ניתן להגיע לריפוזיטורי. בדוק את חיבור האינטרנט.",
    "This is the newest version.": "זו הגרסה החדשה ביותר.",
    "Version {v} is available.": "גרסה {v} זמינה.",
    "Updating": "מעדכן",
    "Downloading\u2026": "מוריד…",
    "Unpacking\u2026": "פורק…",
    "Replacing files\u2026": "מחליף קבצים…",
    "Restarting the agent\u2026": "מפעיל מחדש את הסוכן…",
    "Update failed": "העדכון נכשל",
    "Nothing to update": "אין מה לעדכן",
    "The files are already up to date.": "הקבצים כבר מעודכנים.",
    "Updated": "עודכן",
    "{n} files updated.\n\nThis version also adds these actions:\n{list}\n\n"
    "Use Add actions to put them on the console.":
        "{n} קבצים עודכנו.\n\nהגרסה הזו מוסיפה גם את הפעולות הבאות:\n{list}\n\n"
        "השתמש ב\u05be'הוספת פעולות' כדי להוסיף אותן.",
    "{n} files updated. No new actions in this version.":
        "{n} קבצים עודכנו. אין פעולות חדשות בגרסה הזו.",

    # --- installer, steps ---
    "Set the console password": "קביעת סיסמת כניסה",
    "You will type this every time you open ClassCtl. It is stored as a hash, "
    "so nobody can read it back out of the files.":
        "תקליד אותה בכל פתיחה של התוכנה. היא נשמרת כ-hash, כך שאי אפשר לשחזר "
        "אותה מהקבצים.",
    "Password": "סיסמה",
    "Confirm password": "אימות סיסמה",
    "Next": "הבא",
    "Back": "חזרה",
    "Password required": "נדרשת סיסמה",
    "Enter a password to continue.": "הזן סיסמה כדי להמשיך.",
    "Passwords differ": "הסיסמאות אינן תואמות",
    "The two passwords do not match. Type the second one again.":
        "שתי הסיסמאות אינן תואמות. הקלד את השנייה מחדש.",
    "Location and starter actions": "מיקום ופעולות התחלתיות",
    "The folder is locked to administrators and hidden, because the network "
    "key lives in it.":
        "התיקייה נעולה למנהלים בלבד ומוסתרת, כי מפתח הרשת נמצא בה.",
    "Choose install location": "בחר מיקום התקנה",
    "Starter actions": "פעולות התחלתיות",
    "Shut down": "כיבוי",
    "Restart": "הפעלה מחדש",
    "Reset network card": "איפוס כרטיס רשת",
    "Close open windows": "סגירת חלונות פתוחים",
    "Wake screens": "הערת מסכים",
    # action buttons, matched from the script file name
    "Shutdown": "כיבוי",
    "Restart": "הפעלה מחדש",
    "Reset network": "איפוס רשת",
    "Close windows": "סגירת חלונות",
    "Wake screens ": "הערת מסכים",
    "Prepare wol": "הכנה ל-WoL",
    "Each one becomes a button. You can add or remove any time.":
        "כל אחת הופכת לכפתור. אפשר להוסיף או להסיר בכל עת.",
    "Ready to install": "מוכן להתקנה",
    "Check this, then install. It takes a few seconds.":
        "עבור על הפרטים ואז התקן. זה לוקח כמה שניות.",
    "This machine": "המחשב הזה",
    "Install to": "התקנה אל",
    "Folder access": "גישה לתיקייה",
    "Administrators only, hidden": "מנהלים בלבד, מוסתרת",
    "Agent": "סוכן",
    "Runs as SYSTEM at every boot": "רץ כ-SYSTEM בכל הפעלה",
    "Runs as a systemd service": "רץ כשירות systemd",
    "Firewall": "חומת אש",
    "Actions": "פעולות",
    "none": "ללא",
    "Installing": "מתקין",
    "Starting…": "מתחיל…",
    "Creating the protected folder…": "יוצר את התיקייה המוגנת…",
    "Copying program files…": "מעתיק קבצים…",
    "Registering the agent and firewall rules…":
        "רושם את הסוכן ואת חוקי חומת האש…",
    "Installing the systemd service…": "מתקין את שירות ה-systemd…",
    "Creating shortcuts…": "יוצר קיצורי דרך…",
    "Checking that the agent answers…": "בודק שהסוכן עונה…",
    "Done": "הסתיים",
    "Installed": "הותקן",
    "Installed with a warning": "הותקן עם אזהרה",
    "Open ClassCtl from the Start menu or the desktop.":
        "פתח את ClassCtl מתפריט התחל או משולחן העבודה.",
    "Setup finished, but the agent did not answer on its port yet. Reboot, "
    "then open ClassCtl and press Rescan.":
        "ההתקנה הסתיימה, אך הסוכן עדיין לא ענה בפורט שלו. הפעל מחדש, ואז פתח "
        "את ClassCtl ולחץ על סריקה מחדש.",
    "Scripts folder": "תיקיית סקריפטים",
    "For your image": "לגלופה",
    "Actions created": "פעולות שנוצרו",
    "listening": "מאזין",
    "not answering yet": "עדיין לא עונה",
    "Copy agent.json and controller.json to every other PC so they all share "
    "one network key.":
        "העתק את agent.json ואת controller.json לכל שאר המחשבים, כדי שכולם "
        "יחלקו מפתח רשת אחד.",
    "Access denied": "אין הרשאה",
    "Run the installer as administrator and try again.":
        "הפעל את ההתקנה כמנהל ונסה שוב.",
    "Run this as administrator and try again.": "הפעל כמנהל ונסה שוב.",
    "Install failed": "ההתקנה נכשלה",
    "Something went wrong": "משהו השתבש",
    "Not running as administrator - the protected folder and the agent "
    "service will fail.":
        "לא רץ כמנהל — התיקייה המוגנת ושירות הסוכן ייכשלו.",

    # --- add actions / uninstall ---
    "Only the ones you do not have yet are listed. Each becomes a button.":
        "מוצגות רק הפעולות שעדיין אין לך. כל אחת הופכת לכפתור.",
    "Every starter action is already installed.":
        "כל הפעולות ההתחלתיות כבר מותקנות.",
    "Tick at least one action.": "סמן לפחות פעולה אחת.",
    "Could not add": "לא ניתן להוסיף",
    "Actions added": "הפעולות נוספו",
    "Uninstall ClassCtl": "הסרת ClassCtl",
    "This undoes everything the installer changed on this computer.":
        "הפעולה מבטלת כל שינוי שההתקנה ביצעה במחשב הזה.",
    "Stops the agent and removes its scheduled task":
        "עוצר את הסוכן ומוחק את המשימה המתוזמנת שלו",
    "Closes TCP 48720 and UDP 48719 in the firewall":
        "סוגר את TCP 48720 ו-UDP 48719 בחומת האש",
    "Removes the desktop and Start menu shortcuts":
        "מסיר את הקיצורים משולחן העבודה ומתפריט התחל",
    "Removes the Add/Remove Programs entry":
        "מסיר את הרישום מהוספה/הסרה של תוכניות",
    "Remove ClassCtl from this computer?\nThe password and the network key "
    "are deleted with it.":
        "להסיר את ClassCtl מהמחשב הזה?\nהסיסמה ומפתח הרשת יימחקו יחד איתו.",
    "Uninstalling": "מסיר",
    "Working…": "עובד…",
    "Uninstalled": "הוסר",
    "ClassCtl has been removed from this computer.":
        "ClassCtl הוסר מהמחשב הזה.",
    "Uninstall problem": "תקלה בהסרה",
    "Something did not complete. Reboot and try again.":
        "משהו לא הושלם. הפעל מחדש ונסה שוב.",

    "Added: {list}": "\u05e0\u05d5\u05e1\u05e4\u05d5: {list}",
    "Choose": "\u05d1\u05d7\u05e8",
    "Open": "\u05e4\u05ea\u05d7",
    "Use this folder": "\u05d4\u05e9\u05ea\u05de\u05e9 \u05d1\u05ea\u05d9\u05e7\u05d9\u05d9\u05d4 \u05d6\u05d5",
    "Show all file types": "\u05d4\u05e6\u05d2 \u05db\u05dc \u05e1\u05d5\u05d2\u05d9 \u05d4\u05e7\u05d1\u05e6\u05d9\u05dd",

    # --- generic ---
    "OK": "אישור",
    "Confirm": "אישור",
}

TRANSLATIONS = {"he": HEBREW}


def set_lang(code: str) -> None:
    global _LANG
    _LANG = code if code in TRANSLATIONS or code == "en" else "en"


def get_lang() -> str:
    return _LANG


def is_rtl() -> bool:
    return _LANG == "he"


def t(text: str, **kw) -> str:
    """Translate, then fill in any {placeholders}."""
    out = TRANSLATIONS.get(_LANG, {}).get(text, text)
    if kw:
        try:
            out = out.format(**kw)
        except Exception:
            pass
    return out


# ---------- layout mirroring ----------
def side(default: str) -> str:
    """Mirror a pack side. Buttons keep their meaning in both directions."""
    if not is_rtl():
        return default
    return {"left": "right", "right": "left"}.get(default, default)


def anchor(default: str = "w") -> str:
    if not is_rtl():
        return default
    return {"w": "e", "e": "w", "nw": "ne", "ne": "nw",
            "sw": "se", "se": "sw"}.get(default, default)


def justify(default: str = "left") -> str:
    if not is_rtl():
        return default
    return {"left": "right", "right": "left"}.get(default, default)


def column(index: int, per_row: int) -> int:
    """Fill grids from the right in Hebrew, so station 1 sits where the eye starts."""
    return (per_row - 1 - index) if is_rtl() else index
