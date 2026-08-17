"""
Master Zoho Migration Script
Combined from all iterative migration fixes.
"""

import os, sys, csv, json, pymysql, re, uuid
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()

def get_db_connection():
    return pymysql.connect(
        host=os.getenv("DB_SERVER"), port=int(os.getenv("DB_PORT", "3306")),
        user=os.getenv("DB_USER"), password=os.getenv("DB_PASSWORD"), db=os.getenv("DB_NAME"),
        charset="utf8mb4", ssl={"ssl_disabled": False}, autocommit=False,
        read_timeout=900, write_timeout=900
    )


# =========================================
# SOURCE: zoho_csv_migrator_v4.py
# =========================================
"""
zoho_csv_migrator_v4.py - Bulk-insert version using raw SQL
Uses pymysql directly for maximum speed with Azure MySQL.
Batch size = 100 rows per INSERT statement.
"""
import sys
import os
import csv
import logging
import uuid
import re
import html
from datetime import datetime

sys.path.append(os.path.abspath('.'))


logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
logger = logging.getLogger("migrator_v4")

CSV_DIR = "C:/Users/trucs/Downloads/60018503582_portaldata_147182000003471023"
BATCH = 100

# ── Connection ────────────────────────────────────────────────────────────────
def get_conn():
    host   = os.getenv("DB_SERVER", "trucs-internal-projects-database.mysql.database.azure.com")
    port   = int(os.getenv("DB_PORT", "3306"))
    user   = os.getenv("DB_USER", "trucsadmin")
    pwd    = os.getenv("DB_PASSWORD", "")
    dbname = os.getenv("DB_NAME", "zohoprojects")

    return pymysql.connect(
        host=host,
        port=port,
        user=user,
        password=pwd,
        db=dbname,
        charset="utf8mb4",
        ssl={"ssl_disabled": False},  # Azure requires SSL
        connect_timeout=60,
        read_timeout=900,
        write_timeout=900,
        autocommit=False,
    )

# ── Helpers ───────────────────────────────────────────────────────────────────
def esc(v):
    """Return SQL-safe string or NULL."""
    if v is None or v == "":
        return "NULL"
    v = str(v).replace("\\", "\\\\").replace("'", "\\'").replace("\0", "")
    return f"'{v}'"

def parse_date(s):
    if not s:
        return None
    for fmt in ("%m/%d/%Y %I:%M %p", "%m/%d/%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s.strip(), fmt)
        except:
            pass
    return None

def dt_esc(s):
    d = parse_date(s)
    if d is None:
        return "NULL"
    return f"'{d.strftime('%Y-%m-%d %H:%M:%S')}'"

def strip_html(text):
    if not text:
        return ""
    clean = re.compile('<.*?>')
    return html.unescape(re.sub(clean, '', str(text))).strip()

def bulk_exec(cur, sql_list):
    for sql in sql_list:
        try:
            cur.execute(sql)
        except Exception as e:
            logger.warning(f"Row skipped: {e}")

def batch_insert(cur, table, cols, rows):
    """Insert rows in batches of BATCH. Each row is a tuple of SQL-escaped strings."""
    if not rows:
        return
    col_str = ", ".join(cols)
    for i in range(0, len(rows), BATCH):
        chunk = rows[i:i+BATCH]
        vals = ", ".join(f"({', '.join(r)})" for r in chunk)
        sql = f"INSERT IGNORE INTO {table} ({col_str}) VALUES {vals}"
        try:
            cur.execute(sql)
        except Exception as e:
            logger.warning(f"Batch insert error on {table}: {e}")

# ── Main ──────────────────────────────────────────────────────────────────────
def run_step_0_zoho_csv_migrator_v4():
    conn = get_conn()
    cur = conn.cursor()

    try:
        # ── Truncate ──────────────────────────────────────────────────────────
        logger.info("Truncating tables...")
        cur.execute("SET FOREIGN_KEY_CHECKS = 0")
        for t in ["timelogs", "issue_followers", "issue_assignees", "task_assignees",
                  "task_owners", "project_members", "issues", "tasks",
                  "task_lists", "milestones", "projects"]:
            cur.execute(f"TRUNCATE TABLE {t}")
        cur.execute("SET FOREIGN_KEY_CHECKS = 1")
        conn.commit()

        # ── Load lookup data ──────────────────────────────────────────────────
        cur.execute("SELECT email, id FROM users WHERE email IS NOT NULL")
        email_to_id = {row[0].lower(): row[1] for row in cur.fetchall()}

        cur.execute("SELECT category, label, id FROM master_lookups")
        master = {}
        for cat, lbl, mid in cur.fetchall():
            master.setdefault(cat, {})[lbl.lower()] = mid

        def ml(cat, lbl, default=None):
            if not lbl: return default
            return master.get(cat, {}).get(lbl.lower(), default)

        z_pid = {}   # zoho_project_id -> db_project_id
        z_mid = {}   # zoho_milestone_id -> db_milestone_id
        z_tlid = {}  # zoho_tasklist_id -> db_tasklist_id
        z_tid = {}   # zoho_task_id -> db_task_id
        z_iid = {}   # zoho_issue_id -> db_issue_id

        db_project_names = set()
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

        # ── Projects ──────────────────────────────────────────────────────────
        logger.info("Importing Projects...")
        with open(os.path.join(CSV_DIR, "Project.csv"), "r", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))

        for row in rows:
            zid   = row.get("Project Id", "").strip()
            name  = strip_html(row.get("Project Name", "")).strip()[:200]
            owner_email = row.get("Owner", "").strip().lower()
            owner_id = email_to_id.get(owner_email, "NULL")

            orig = name
            sfx = 2
            while name in db_project_names:
                name = f"{orig} (Z-{zid})"
                if name in db_project_names:
                    name = f"{orig} ({sfx})"; sfx += 1
            db_project_names.add(name)

            status_id = ml("ProjectStatus", row.get("Project Status"), 1)

            cur.execute(
                f"""INSERT INTO projects
                    (project_id_sync, public_id, account_name, project_name, customer_name,
                     client_name, billing_model, project_type, owner_id, status_id, description,
                     estimated_hours, actual_hours, is_archived, is_template, is_group,
                     is_processed, is_active, is_deleted, created_at)
                    VALUES ({esc(zid)}, {esc('PRJ-'+zid)}, '', {esc(name)}, '', '', '', '',
                            {owner_id if owner_id != 'NULL' else 'NULL'},
                            {status_id if status_id else 'NULL'},
                            {esc(strip_html(row.get("Project Overview","")))},
                            0, 0, 0, 0, 0, 0, 1, 0, '{now}')"""
            )
            db_id = cur.lastrowid
            z_pid[zid] = db_id

            if owner_id and owner_id != "NULL":
                cur.execute(
                    f"INSERT IGNORE INTO project_members (project_id, user_id, project_profile) "
                    f"VALUES ({db_id}, {owner_id}, 'Project Lead')"
                )

        conn.commit()
        logger.info(f"Imported {len(z_pid)} projects.")

        # ── Milestones ────────────────────────────────────────────────────────
        logger.info("Importing Milestones...")
        with open(os.path.join(CSV_DIR, "Milestone.csv"), "r", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))

        mil_rows = []
        for row in rows:
            zid  = row.get("Milestone Id", "").strip()
            zpid = row.get("Project Id", "").strip()
            db_pid = z_pid.get(zpid)
            if not db_pid: continue
            mil_rows.append((
                esc("MIL-"+zid), esc(strip_html(row.get("Milestone Name",""))), str(db_pid),
                dt_esc(row.get("Start Date")), dt_esc(row.get("End Date")),
                "1", "0", f"'{now}'"
            ))

        batch_insert(cur, "milestones",
            ["public_id","milestone_name","project_id","start_date","end_date","is_active","is_deleted","created_at"],
            mil_rows)
        conn.commit()

        # re-fetch milestone IDs
        with open(os.path.join(CSV_DIR, "Milestone.csv"), "r", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                zid  = row.get("Milestone Id", "").strip()
                zpid = row.get("Project Id", "").strip()
                if not z_pid.get(zpid): continue
                cur.execute(f"SELECT id FROM milestones WHERE public_id = 'MIL-{zid}' LIMIT 1")
                r = cur.fetchone()
                if r: z_mid[zid] = r[0]
        logger.info(f"Imported {len(z_mid)} milestones.")

        # ── Task Lists ────────────────────────────────────────────────────────
        logger.info("Importing Task Lists...")
        with open(os.path.join(CSV_DIR, "TaskList.csv"), "r", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))

        # Try alternate filename
        if not rows:
            with open(os.path.join(CSV_DIR, "Tasklist.csv"), "r", encoding="utf-8-sig") as f:
                rows = list(csv.DictReader(f))

        tl_rows = []
        tl_zids = []
        for row in rows:
            zid  = row.get("Tasklist Id", "").strip() or row.get("TaskList Id", "").strip()
            zpid = row.get("Project Id", "").strip()
            zmid = row.get("Milestone Id", "").strip()
            db_pid = z_pid.get(zpid)
            if not db_pid: continue
            db_mid = z_mid.get(zmid, "NULL")
            tl_rows.append((
                esc(strip_html(row.get("Tasklist Name", row.get("TaskList Name","")))),
                str(db_pid),
                str(db_mid) if db_mid != "NULL" else "NULL",
                "1", "0", f"'{now}'"
            ))
            tl_zids.append(zid)

        batch_insert(cur, "task_lists",
            ["name","project_id","milestone_id","is_active","is_deleted","created_at"],
            tl_rows)
        conn.commit()

        # Re-map tasklist IDs by position (since we used IGNORE they should be sequential)
        cur.execute(f"SELECT id FROM task_lists ORDER BY id ASC LIMIT {len(tl_rows)+10}")
        tl_db_ids = [r[0] for r in cur.fetchall()]
        for i, zid in enumerate(tl_zids):
            if i < len(tl_db_ids):
                z_tlid[zid] = tl_db_ids[i]
        logger.info(f"Imported {len(z_tlid)} task lists.")

        # ── Tasks ─────────────────────────────────────────────────────────────
        logger.info("Importing Tasks...")
        with open(os.path.join(CSV_DIR, "Task.csv"), "r", encoding="utf-8-sig") as f:
            task_rows_csv = list(csv.DictReader(f))

        seen_task = {}
        task_insert_rows = []
        task_zids = []
        task_pids = []

        for row in task_rows_csv:
            zid  = row.get("Task Id", "").strip() or f"gen-{uuid.uuid4().hex[:8]}"
            zpid = row.get("Associated ProjectId", "").strip()
            db_pid = z_pid.get(zpid)
            if not db_pid: continue

            name = strip_html(row.get("Task", "")).strip()
            if len(name) > 200: name = name[:200] + "..."
            key = (db_pid, name)
            if key in seen_task:
                name = f"{name} (Z-{zid})"[:255]
            seen_task[key] = True

            creator_email = row.get("Created by", "").strip().lower()
            owner_raw     = row.get("Owner", "").strip()
            owner_emails  = [e.strip().lower() for e in owner_raw.split(",") if e.strip()]
            creator_id = email_to_id.get(creator_email)
            first_owner_id = email_to_id.get(owner_emails[0]) if owner_emails else None

            tl_id   = z_tlid.get(row.get("Tasklist Id","").strip())
            mil_id  = z_mid.get(row.get("Milestone Id","").strip())
            status_id   = ml("TaskStatus", row.get("Status"), 4)
            priority_id = ml("Priority",   row.get("Priority"), 8)

            task_insert_rows.append((
                esc(f"TSK-{zid}"),
                esc(name),
                esc(strip_html(row.get("Description",""))),
                str(db_pid),
                str(tl_id) if tl_id else "NULL",
                str(mil_id) if mil_id else "NULL",
                str(creator_id) if creator_id else "NULL",
                str(first_owner_id) if first_owner_id else "NULL",
                str(first_owner_id) if first_owner_id else "NULL",
                str(status_id) if status_id else "NULL",
                str(priority_id) if priority_id else "NULL",
                dt_esc(row.get("Start Date")),
                dt_esc(row.get("End Date")),
                "0", "'Billable'", "0", "0", "0", "1", "0", f"'{now}'"
            ))
            task_zids.append(zid)
            task_pids.append(db_pid)

        cols_t = ["public_id","task_name","description","project_id","task_list_id","milestone_id",
                  "created_by_id","assignee_id","owner_id","status_id","priority_id",
                  "start_date","due_date","completion_percentage","billing_type",
                  "work_hours","cached_timelog_total","is_processed","is_active","is_deleted","created_at"]

        for i in range(0, len(task_insert_rows), BATCH):
            chunk = task_insert_rows[i:i+BATCH]
            vals  = ", ".join(f"({', '.join(r)})" for r in chunk)
            try:
                cur.execute(f"INSERT IGNORE INTO tasks ({', '.join(cols_t)}) VALUES {vals}")
                conn.commit()
                if (i // BATCH) % 10 == 0:
                    logger.info(f"  Tasks: {min(i+BATCH, len(task_insert_rows))}/{len(task_insert_rows)}")
            except Exception as e:
                logger.warning(f"Task batch {i//BATCH} error: {e}")
                conn.rollback()

        logger.info(f"Imported {len(task_insert_rows)} tasks.")

        # Re-fetch task IDs
        cur.execute("SELECT public_id, id FROM tasks")
        for pub_id, db_id in cur.fetchall():
            if pub_id.startswith("TSK-"):
                zid = pub_id[4:]
                z_tid[zid] = db_id

        # Add task users to project_members
        pm_rows = []
        for row in task_rows_csv:
            zpid = row.get("Associated ProjectId","").strip()
            db_pid = z_pid.get(zpid)
            if not db_pid: continue
            for email in [row.get("Created by","").strip().lower(), *[e.strip().lower() for e in row.get("Owner","").split(",") if e.strip()]]:
                uid = email_to_id.get(email)
                if uid:
                    pm_rows.append((str(db_pid), str(uid), "'Member'", "'User'"))
        batch_insert(cur, "project_members",
            ["project_id","user_id","project_profile","portal_profile"],
            pm_rows)
        conn.commit()

        # ── Issues ────────────────────────────────────────────────────────────
        logger.info("Importing Issues...")
        with open(os.path.join(CSV_DIR, "Issue.csv"), "r", encoding="utf-8-sig") as f:
            issue_rows_csv = list(csv.DictReader(f))

        seen_issue = {}
        issue_insert_rows = []

        for row in issue_rows_csv:
            zid  = row.get("IssueId","").strip() or f"gen-{uuid.uuid4().hex[:8]}"
            zpid = row.get("Project Id","").strip()
            db_pid = z_pid.get(zpid)
            if not db_pid: continue

            name = strip_html(row.get("Title","")).strip()
            if len(name) > 200: name = name[:200] + "..."
            key = (db_pid, name)
            if key in seen_issue:
                name = f"{name} (Z-{zid})"[:255]
            seen_issue[key] = True

            assignee_email = row.get("Assignee","").strip().lower()
            reporter_email = row.get("Reporter","").strip().lower()
            assignee_id = email_to_id.get(assignee_email)
            reporter_id = email_to_id.get(reporter_email)

            status_id  = ml("IssueStatus",   row.get("Status"),         10)
            priority_id= ml("Priority",       row.get("Priority"),        8)
            sev_id     = ml("Severity",       row.get("Severity"),       18)
            cls_id     = ml("Classification", row.get("Classification"), 15)

            issue_insert_rows.append((
                esc(f"ISS-{zid}"),
                esc(name),
                esc(strip_html(row.get("Description",""))),
                str(db_pid),
                str(reporter_id) if reporter_id else "NULL",
                str(assignee_id) if assignee_id else "NULL",
                str(status_id)   if status_id   else "NULL",
                str(priority_id) if priority_id else "NULL",
                str(sev_id)      if sev_id      else "NULL",
                str(cls_id)      if cls_id      else "NULL",
                "1", "0", "0", f"'{now}'"
            ))

        cols_i = ["public_id","bug_name","description","project_id",
                  "reporter_id","assignee_id","status_id","priority_id",
                  "severity_id","classification_id",
                  "is_active","is_deleted","is_processed","created_at"]

        for i in range(0, len(issue_insert_rows), BATCH):
            chunk = issue_insert_rows[i:i+BATCH]
            vals  = ", ".join(f"({', '.join(r)})" for r in chunk)
            try:
                cur.execute(f"INSERT IGNORE INTO issues ({', '.join(cols_i)}) VALUES {vals}")
                conn.commit()
                if (i // BATCH) % 10 == 0:
                    logger.info(f"  Issues: {min(i+BATCH, len(issue_insert_rows))}/{len(issue_insert_rows)}")
            except Exception as e:
                logger.warning(f"Issue batch {i//BATCH} error: {e}")
                conn.rollback()

        logger.info(f"Imported {len(issue_insert_rows)} issues.")

        # Re-fetch issue IDs
        cur.execute("SELECT public_id, id FROM issues")
        for pub_id, db_id in cur.fetchall():
            if pub_id.startswith("ISS-"):
                z_iid[pub_id[4:]] = db_id

        # ── Timelogs ──────────────────────────────────────────────────────────
        logger.info("Importing Timelogs...")
        with open(os.path.join(CSV_DIR, "Timesheet.csv"), "r", encoding="utf-8-sig") as f:
            tl_rows_csv = list(csv.DictReader(f))

        timelog_insert_rows = []
        for row in tl_rows_csv:
            zpid = row.get("Project Id","").strip()
            db_pid = z_pid.get(zpid)
            if not db_pid: continue

            user_email = row.get("EmailId","").strip().lower()
            user_id = email_to_id.get(user_email)
            if not user_id: continue

            d = parse_date(row.get("Date",""))
            if not d: continue
            log_date = d.strftime("%Y-%m-%d")

            type_val = row.get("Type","").strip().lower()
            tb_id    = row.get("Task/Bug Id","").strip()
            tid = z_tid.get(tb_id) if type_val == "task" else None
            iid = z_iid.get(tb_id) if type_val in ("issue","bug") else None

            hours_raw = row.get("Time Log","0").strip() or "0"

            timelog_insert_rows.append((
                str(db_pid),
                str(tid) if tid else "NULL",
                str(iid) if iid else "NULL",
                str(user_id),
                esc(hours_raw),
                f"'{log_date}'",
                esc(strip_html(row.get("Notes",""))),
                "1", "0", f"'{now}'"
            ))

        cols_tl = ["project_id","task_id","issue_id","user_id","daily_log_hours","date",
                   "notes","is_active","is_deleted","created_at"]

        for i in range(0, len(timelog_insert_rows), BATCH):
            chunk = timelog_insert_rows[i:i+BATCH]
            vals  = ", ".join(f"({', '.join(r)})" for r in chunk)
            try:
                cur.execute(f"INSERT IGNORE INTO timelogs ({', '.join(cols_tl)}) VALUES {vals}")
                conn.commit()
            except Exception as e:
                logger.warning(f"Timelog batch {i//BATCH} error: {e}")
                conn.rollback()

        logger.info(f"Imported {len(timelog_insert_rows)} timelogs.")
        logger.info("✅ Migration Complete!")

    except Exception as e:
        conn.rollback()
        logger.error(f"Fatal error: {e}")
        import traceback; traceback.print_exc()
    finally:
        cur.close()
        conn.close()



# =========================================
# SOURCE: clean_duplicates.py
# =========================================
import sys
import os
import logging
sys.path.append(os.path.abspath('.'))

from app.core.database import SessionLocal
from app.models.project import Project, ProjectMember
from app.models.task import Task
from app.models.issue import Issue
from app.models.milestone import Milestone
from app.models.task_list import TaskList
from app.models.timelog import TimeLog
from sqlalchemy import text, select, func

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
logger = logging.getLogger("clean_duplicates")

def clean_projects(db):
    logger.info("--- Cleaning Projects ---")
    res = db.execute(text("SELECT project_name FROM projects GROUP BY project_name HAVING COUNT(*) > 1")).fetchall()
    dup_names = [r[0] for r in res]
    logger.info(f"Found {len(dup_names)} duplicate project names")
    
    for name in dup_names:
        projs = db.execute(select(Project).where(Project.project_name == name).order_by(Project.id)).scalars().all()
        if len(projs) <= 1: continue
        primary = projs[0]
        duplicates = projs[1:]
        
        dup_ids = [p.id for p in duplicates]
        logger.info(f"Merging {len(dup_ids)} duplicates into Project '{name}' (ID: {primary.id})")
        
        # Update references
        db.execute(text(f"UPDATE milestones SET project_id = {primary.id} WHERE project_id IN ({','.join(map(str, dup_ids))})"))
        db.execute(text(f"UPDATE task_lists SET project_id = {primary.id} WHERE project_id IN ({','.join(map(str, dup_ids))})"))
        db.execute(text(f"UPDATE tasks SET project_id = {primary.id} WHERE project_id IN ({','.join(map(str, dup_ids))})"))
        db.execute(text(f"UPDATE issues SET project_id = {primary.id} WHERE project_id IN ({','.join(map(str, dup_ids))})"))
        db.execute(text(f"UPDATE timelogs SET project_id = {primary.id} WHERE project_id IN ({','.join(map(str, dup_ids))})"))
        
        # Move project members (ignore integrity errors if they already exist in primary)
        for d_id in dup_ids:
            try:
                db.execute(text(f"UPDATE IGNORE project_members SET project_id = {primary.id} WHERE project_id = {d_id}"))
            except:
                pass
            
        db.execute(text(f"DELETE FROM projects WHERE id IN ({','.join(map(str, dup_ids))})"))
            
    db.commit()

def clean_tasks(db):
    logger.info("--- Cleaning Tasks ---")
    res = db.execute(text("SELECT project_id, task_name FROM tasks GROUP BY project_id, task_name HAVING COUNT(*) > 1")).fetchall()
    logger.info(f"Found {len(res)} duplicate task names")
    
    for pid, tname in res:
        tasks = db.execute(select(Task).where(Task.project_id == pid, Task.task_name == tname).order_by(Task.id)).scalars().all()
        if len(tasks) <= 1: continue
        primary = tasks[0]
        duplicates = tasks[1:]
        
        dup_ids = [t.id for t in duplicates]
        
        # Update timelogs
        db.execute(text(f"UPDATE timelogs SET task_id = {primary.id} WHERE task_id IN ({','.join(map(str, dup_ids))})"))
        
        # Update assignees (using IGNORE to prevent duplicate entries for primary task)
        for d_id in dup_ids:
            try:
                db.execute(text(f"UPDATE IGNORE task_owners SET task_id = {primary.id} WHERE task_id = {d_id}"))
                db.execute(text(f"UPDATE IGNORE task_assignees SET task_id = {primary.id} WHERE task_id = {d_id}"))
            except:
                pass
                
        db.execute(text(f"DELETE FROM tasks WHERE id IN ({','.join(map(str, dup_ids))})"))
            
    db.commit()

def clean_issues(db):
    logger.info("--- Cleaning Issues ---")
    res = db.execute(text("SELECT project_id, bug_name FROM issues GROUP BY project_id, bug_name HAVING COUNT(*) > 1")).fetchall()
    logger.info(f"Found {len(res)} duplicate issue names")
    
    for pid, iname in res:
        issues = db.execute(select(Issue).where(Issue.project_id == pid, Issue.bug_name == iname).order_by(Issue.id)).scalars().all()
        if len(issues) <= 1: continue
        primary = issues[0]
        duplicates = issues[1:]
        
        dup_ids = [i.id for i in duplicates]
        
        # Update timelogs
        db.execute(text(f"UPDATE timelogs SET issue_id = {primary.id} WHERE issue_id IN ({','.join(map(str, dup_ids))})"))
        
        for d_id in dup_ids:
            try:
                db.execute(text(f"UPDATE IGNORE issue_followers SET issue_id = {primary.id} WHERE issue_id = {d_id}"))
                db.execute(text(f"UPDATE IGNORE issue_assignees SET issue_id = {primary.id} WHERE issue_id = {d_id}"))
            except:
                pass
                
        # Delete duplicates using raw SQL to bypass SQLAlchemy UOW stale data errors
        db.execute(text(f"DELETE FROM issues WHERE id IN ({','.join(map(str, dup_ids))})"))
            
    db.commit()

def run_step_1_clean_duplicates():
    db = SessionLocal()
    try:
        clean_projects(db)
        clean_tasks(db)
        clean_issues(db)
        logger.info("All duplicates cleaned successfully.")
    except Exception as e:
        db.rollback()
        logger.error(f"Error: {e}")
    finally:
        db.close()



# =========================================
# SOURCE: fix_users.py
# =========================================
import sys
import os
import csv
import logging
from sqlalchemy import text, select

sys.path.append(os.path.abspath('.'))
from app.core.database import SessionLocal
from app.models.project import Project, ProjectMember
from app.models.task import Task
from app.models.issue import Issue
from app.models.user import User

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
logger = logging.getLogger("fix_users")

CSV_DIR = "C:/Users/trucs/Downloads/60018503582_portaldata_147182000003471023"

def run_step_2_fix_users():
    db = SessionLocal()
    try:
        # Build email to User ID map
        users = db.execute(select(User)).scalars().all()
        email_to_id = {u.email.lower(): u.id for u in users if u.email}
        logger.info(f"Loaded {len(email_to_id)} users from database.")
        
        # We need a robust way to map Zoho Project ID to DB Project ID.
        # Since we merged duplicates by name, we can map Zoho Project ID -> Project Name -> DB Project ID
        z_pid_to_name = {}
        with open(os.path.join(CSV_DIR, "Project.csv"), "r", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                z_id = row.get("Project Id", "").strip()
                name = strip_html(row.get("Project Name", "")).strip()
                if z_id and name:
                    z_pid_to_name[z_id] = name
                    
        name_to_db_pid = {}
        projects = db.execute(select(Project)).scalars().all()
        for p in projects:
            name_to_db_pid[p.project_name] = p.id
            
        z_pid_to_db_pid = {z_id: name_to_db_pid.get(name) for z_id, name in z_pid_to_name.items() if name in name_to_db_pid}

        # Fix Projects
        logger.info("Fixing Projects...")
        with open(os.path.join(CSV_DIR, "Project.csv"), "r", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                z_id = row.get("Project Id", "").strip()
                owner_email = row.get("Owner", "").strip().lower()
                db_pid = z_pid_to_db_pid.get(z_id)
                owner_id = email_to_id.get(owner_email)
                
                if db_pid and owner_id:
                    db.execute(text(f"UPDATE projects SET owner_id = {owner_id} WHERE id = {db_pid}"))
                    try:
                        db.execute(text(f"INSERT IGNORE INTO project_members (project_id, user_id) VALUES ({db_pid}, {owner_id})"))
                    except:
                        pass
        db.commit()

        # Fix Tasks
        logger.info("Fixing Tasks...")
        with open(os.path.join(CSV_DIR, "Task.csv"), "r", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                z_pid = row.get("Associated ProjectId", "").strip()
                task_name = strip_html(row.get("Task", "")).strip()
                db_pid = z_pid_to_db_pid.get(z_pid)
                
                owner_emails = [e.strip().lower() for e in row.get("Owner", "").split(",") if e.strip()]
                created_by_email = row.get("Created by", "").strip().lower()
                
                if not db_pid: continue
                
                # Because we merged tasks by (project_id, task_name), there's only 1 task in the DB for this pair.
                # However, we can just update it using a subquery or by finding it.
                t = db.execute(select(Task.id).where(Task.project_id == db_pid, Task.task_name == task_name)).scalar()
                if not t: continue
                
                creator_id = email_to_id.get(created_by_email)
                if creator_id:
                    db.execute(text(f"UPDATE tasks SET created_by_id = {creator_id} WHERE id = {t}"))
                    try: db.execute(text(f"INSERT IGNORE INTO project_members (project_id, user_id) VALUES ({db_pid}, {creator_id})"))
                    except: pass

                if owner_emails:
                    first_owner_id = email_to_id.get(owner_emails[0])
                    if first_owner_id:
                        db.execute(text(f"UPDATE tasks SET assignee_id = {first_owner_id}, owner_id = {first_owner_id} WHERE id = {t}"))
                        try: db.execute(text(f"INSERT IGNORE INTO project_members (project_id, user_id) VALUES ({db_pid}, {first_owner_id})"))
                        except: pass
        db.commit()
        
        # Fix Issues
        logger.info("Fixing Issues...")
        with open(os.path.join(CSV_DIR, "Issue.csv"), "r", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                z_pid = row.get("Project ID", "").strip()
                bug_name = row.get("Issue Title", "").strip()
                db_pid = z_pid_to_db_pid.get(z_pid)
                
                assignee_email = row.get("Assignee", "").strip().lower()
                reporter_email = row.get("Reporter", "").strip().lower()
                
                if not db_pid: continue
                
                i_id = db.execute(select(Issue.id).where(Issue.project_id == db_pid, Issue.bug_name == bug_name)).scalar()
                if not i_id: continue
                
                assignee_id = email_to_id.get(assignee_email)
                reporter_id = email_to_id.get(reporter_email)
                
                if assignee_id:
                    db.execute(text(f"UPDATE issues SET assignee_id = {assignee_id} WHERE id = {i_id}"))
                    try: db.execute(text(f"INSERT IGNORE INTO project_members (project_id, user_id) VALUES ({db_pid}, {assignee_id})"))
                    except: pass
                    
                if reporter_id:
                    db.execute(text(f"UPDATE issues SET reporter_id = {reporter_id} WHERE id = {i_id}"))
                    try: db.execute(text(f"INSERT IGNORE INTO project_members (project_id, user_id) VALUES ({db_pid}, {reporter_id})"))
                    except: pass
        db.commit()

        logger.info("User mapping fixed successfully.")
    except Exception as e:
        db.rollback()
        logger.error(f"Error fixing users: {e}")
    finally:
        db.close()



# =========================================
# SOURCE: fix_all_fields.py
# =========================================
"""
fix_all_fields.py
Comprehensive fix for all field mapping issues after migration.
Fixes: tasks, issues, projects, milestones, timelogs
"""
from datetime import datetime

sys.path.append(os.path.abspath('.'))

CSV_DIR = "C:/Users/trucs/Downloads/60018503582_portaldata_147182000003471023"

def get_conn():
    return pymysql.connect(
        host=os.getenv("DB_SERVER"), port=int(os.getenv("DB_PORT", "3306")),
        user=os.getenv("DB_USER"), password=os.getenv("DB_PASSWORD"), db=os.getenv("DB_NAME"),
        charset="utf8mb4", ssl={"ssl_disabled": False}, autocommit=False,
        read_timeout=900, write_timeout=900,
    )

def parse_date(s):
    if not s or str(s).strip() in ("", "-", "None"):
        return None
    for fmt in ("%m/%d/%Y %I:%M %p", "%m/%d/%Y", "%Y-%m-%d %H:%M:%S",
                "%m-%d-%Y", "%m-%d-%Y %I:%M %p", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s.strip(), fmt)
        except:
            pass
    return None

def ds(s):
    d = parse_date(s)
    return d.strftime("%Y-%m-%d") if d else None

def esc(v):
    if v is None or str(v).strip() in ("", "-", "None"):
        return "NULL"
    v = str(v).replace("\\", "\\\\").replace("'", "\\'").replace("\0", "")
    return "'" + v + "'"

def run_step_3_fix_all_fields():
    conn = get_conn()
    cur = conn.cursor()

    # ── Load master lookups ────────────────────────────────────────────────────
    cur.execute("SELECT id, category, label FROM master_lookups")
    master_raw = cur.fetchall()
    master = {}
    for mid, cat, lbl in master_raw:
        master.setdefault(cat, {})[lbl.lower()] = mid
    
    def ml(cat, lbl, default=None):
        if not lbl or lbl.strip() in ("-", "None", "none", ""):
            return default
        return master.get(cat, {}).get(lbl.strip().lower(), default)

    # ── Load user map ──────────────────────────────────────────────────────────
    cur.execute("SELECT email, id FROM users WHERE email IS NOT NULL")
    email_map = {r[0].lower(): r[1] for r in cur.fetchall()}

    # ── Load project map ───────────────────────────────────────────────────────
    cur.execute("SELECT public_id, id FROM projects WHERE public_id LIKE 'PRJ-%'")
    proj_map = {pub[4:]: db_id for pub, db_id in cur.fetchall()}

    # ── Load task list map (zoho_id -> db_id) ─────────────────────────────────
    cur.execute("SELECT public_id, id FROM milestones WHERE public_id LIKE 'MIL-%'")
    mil_map = {pub[4:]: db_id for pub, db_id in cur.fetchall()}

    # ── 1. FIX PROJECTS ───────────────────────────────────────────────────────
    print("\n=== Fixing Projects ===")
    with open(os.path.join(CSV_DIR, "Project.csv"), "r", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    proj_updated = 0
    for row in rows:
        zpid = row.get("Project Id", "").strip()
        db_pid = proj_map.get(zpid)
        if not db_pid:
            continue

        start_date = ds(row.get("Start Date"))
        end_date   = ds(row.get("End Date"))
        status_label = row.get("Project Status", "").strip()
        # Map Zoho project status to our master_lookups
        status_id = ml("ProjectStatus", status_label)
        created_by_email = row.get("CreatedBy", "").strip().lower()
        created_by_id = email_map.get(created_by_email)

        updates = []
        if start_date and start_date != 'NULL':
            updates.append(f"expected_start_date = {start_date}")
        if end_date and end_date != 'NULL':
            updates.append(f"expected_end_date = {end_date}")
        if status_id:
            updates.append(f"status_id = {status_id}")
        if created_by_id:
            updates.append(f"created_by = {created_by_id}")

        if updates:
            sql = f"UPDATE projects SET {', '.join(updates)} WHERE id = {db_pid}"
            cur.execute(sql)
            proj_updated += 1

        if proj_updated % 50 == 0:
            conn.commit()

    conn.commit()
    print(f"  Updated {proj_updated} projects with dates and status")

    print("\n=== Rebuilding Task List Map ===")
    # Truncate and re-import task_lists so we can build the zoho_id -> db_id mapping
    cur.execute("UPDATE tasks SET task_list_id = NULL")
    conn.commit()
    cur.execute("SET FOREIGN_KEY_CHECKS = 0")
    cur.execute("TRUNCATE TABLE task_lists")
    cur.execute("SET FOREIGN_KEY_CHECKS = 1")
    conn.commit()

    with open(os.path.join(CSV_DIR, "TaskList.csv"), "r", encoding="utf-8-sig") as f:
        tl_rows = list(csv.DictReader(f))

    # Re-load milestone map
    cur.execute("SELECT public_id, id FROM milestones WHERE public_id LIKE 'MIL-%'")
    mil_map2 = {pub[4:]: db_id for pub, db_id in cur.fetchall()}

    z_tlid = {}
    tl_inserted = 0
    for row in tl_rows:
        zid  = row.get("Task ListId", "").strip()
        zpid = row.get("Associated ProjectId", "").strip()
        zmid = row.get("Associated MilestoneId", "").strip()
        db_pid = proj_map.get(zpid)
        if not db_pid:
            continue
        db_mid = mil_map2.get(zmid)
        name = row.get("Task List", "").strip()[:255]
        sql = (
            f"INSERT INTO task_lists (name, project_id, milestone_id, is_active, is_deleted, created_at) "
            f"VALUES ({esc(name)}, {db_pid}, {'NULL' if not db_mid else db_mid}, 1, 0, NOW())"
        )
        cur.execute(sql)
        z_tlid[zid] = cur.lastrowid
        tl_inserted += 1
        if tl_inserted % 100 == 0:
            conn.commit()
    conn.commit()
    print(f"  Re-imported {tl_inserted} task lists with Zoho ID mapping ({len(z_tlid)} mapped)")

    # ── 3. FIX TASKS ─────────────────────────────────────────────────────────
    print("\n=== Fixing Tasks ===")
    with open(os.path.join(CSV_DIR, "Task.csv"), "r", encoding="utf-8-sig") as f:
        task_rows = list(csv.DictReader(f))

    # Load task public_id -> db_id map
    cur.execute("SELECT public_id, id FROM tasks WHERE public_id LIKE 'TSK-%'")
    task_pub_map = {pub[4:]: db_id for pub, db_id in cur.fetchall()}

    task_updated = 0
    for row in task_rows:
        zid  = row.get("TaskId", "").strip()
        db_tid = task_pub_map.get(zid)
        if not db_tid:
            continue

        # Dates
        start_date = ds(row.get("Start Date"))
        end_date   = ds(row.get("End Date"))
        completed_on = ds(row.get("Completed On"))

        # Status - use TaskStatus category
        status_id = ml("TaskStatus", row.get("Status"))
        
        # Priority - use TaskPriority category 
        priority_id = ml("TaskPriority", row.get("Priority"))
        
        # Billing type
        billable = row.get("Billable Type", "Billable").strip()
        if billable not in ("Billable", "Non-Billable", "Internal"):
            billable = "Billable"
        
        # Work hours (from Billable Hours + Non Billable Hours)
        try:
            bh  = float(row.get("Billable Hours", "0") or "0")
            nbh = float(row.get("Non Billable Hours", "0") or "0")
            work_hours = bh + nbh
        except:
            work_hours = 0

        # Completion percentage
        try:
            pct = int(float(row.get("Percentage Complete", "0") or "0"))
        except:
            pct = 0

        # Task list
        ztlid = row.get("Associated Task ListId", "").strip()
        db_tlid = z_tlid.get(ztlid)

        # Milestone
        zmilid = row.get("Associated MilestoneId", "").strip()
        db_milid = mil_map.get(zmilid)

        # Owner
        owner_email = row.get("Owner", "").strip().lower().split(",")[0].strip()
        owner_id = email_map.get(owner_email)

        updates = []
        if start_date and start_date != 'NULL':
            updates.append(f"start_date = {start_date}")
        if end_date and end_date != 'NULL':
            updates.append(f"due_date = {end_date}")
        if completed_on and completed_on != 'NULL':
            updates.append(f"completion_date = {completed_on}")
        if status_id:
            updates.append(f"status_id = {status_id}")
        if priority_id:
            updates.append(f"priority_id = {priority_id}")
        updates.append(f"completion_percentage = {pct}")
        updates.append(f"work_hours = {work_hours}")
        updates.append(f"billing_type = '{billable}'")
        if db_tlid:
            updates.append(f"task_list_id = {db_tlid}")
        if db_milid:
            updates.append(f"milestone_id = {db_milid}")
        if owner_id:
            updates.append(f"owner_id = {owner_id}")
            updates.append(f"assignee_id = {owner_id}")

        if updates:
            sql = f"UPDATE tasks SET {', '.join(updates)} WHERE id = {db_tid}"
            try:
                cur.execute(sql)
            except Exception as e:
                print(f"  Task {zid} error: {e}")
            task_updated += 1

        if task_updated % 200 == 0:
            conn.commit()
            print(f"  Tasks: {task_updated}/{len(task_rows)}")

    conn.commit()
    print(f"  Updated {task_updated} tasks")

    # ── 4. FIX ISSUES ─────────────────────────────────────────────────────────
    print("\n=== Fixing Issues ===")
    with open(os.path.join(CSV_DIR, "Issue.csv"), "r", encoding="utf-8-sig") as f:
        issue_rows = list(csv.DictReader(f))

    cur.execute("SELECT public_id, id FROM issues WHERE public_id LIKE 'ISS-%'")
    issue_pub_map = {pub[4:]: db_id for pub, db_id in cur.fetchall()}

    issue_updated = 0
    for row in issue_rows:
        zid = row.get("IssueId", "").strip()
        db_iid = issue_pub_map.get(zid)
        if not db_iid:
            continue

        due_date = ds(row.get("Due Date"))
        completed_on = ds(row.get("Completed On"))

        # Status - IssueStatus category
        status_id = ml("IssueStatus", row.get("Status"))

        # Priority - IssuePriority / TaskPriority categories
        prio_label = row.get("Priority", "").strip()
        priority_id = ml("TaskPriority", prio_label) or ml("IssueSeverity", prio_label)

        # Severity - IssueSeverity category
        sev_label = row.get("Severity", "").strip()
        if "-" in sev_label:
            sev_label = sev_label.split("-", 1)[1].strip()
        severity_id = ml("IssueSeverity", sev_label)

        # Classification
        cls_label = row.get("Classification", "").strip()
        classification_id = ml("IssueClassification", cls_label)

        # Milestone (release milestone)
        zmilid = row.get("Release MilestoneId", "").strip()
        db_milid = mil_map.get(zmilid)

        # Assignee / Reporter
        assignee_email = row.get("Assignee", "").strip().lower()
        reporter_email = row.get("Reporter", "").strip().lower()
        assignee_id = email_map.get(assignee_email)
        reporter_id = email_map.get(reporter_email)

        updates = []
        if due_date and due_date != 'NULL':
            updates.append(f"due_date = {due_date}")
        if completed_on and completed_on != 'NULL':
            updates.append(f"last_closed_time = {completed_on}")
        if status_id:
            updates.append(f"status_id = {status_id}")
        if priority_id:
            updates.append(f"priority_id = {priority_id}")
        if severity_id:
            updates.append(f"severity_id = {severity_id}")
        if classification_id:
            updates.append(f"classification_id = {classification_id}")
        if db_milid:
            updates.append(f"milestone_id = {db_milid}")
        if assignee_id:
            updates.append(f"assignee_id = {assignee_id}")
        if reporter_id:
            updates.append(f"reporter_id = {reporter_id}")
        flag = row.get("Flag", "").strip()
        if flag and flag != "-":
            updates.append(f"flag = {esc(flag)}")

        if updates:
            sql = f"UPDATE issues SET {', '.join(updates)} WHERE id = {db_iid}"
            try:
                cur.execute(sql)
            except Exception as e:
                print(f"  Issue {zid} error: {e}")
            issue_updated += 1

        if issue_updated % 200 == 0:
            conn.commit()
            print(f"  Issues: {issue_updated}/{len(issue_rows)}")

    conn.commit()
    print(f"  Updated {issue_updated} issues")

    # ── 5. FIX MILESTONES ─────────────────────────────────────────────────────
    print("\n=== Fixing Milestones ===")
    with open(os.path.join(CSV_DIR, "Milestone.csv"), "r", encoding="utf-8-sig") as f:
        mil_rows = list(csv.DictReader(f))

    cur.execute("SELECT public_id, id FROM milestones WHERE public_id LIKE 'MIL-%'")
    mil_pub_map = {pub[4:]: db_id for pub, db_id in cur.fetchall()}

    mil_updated = 0
    for row in mil_rows:
        zid = row.get("MilestoneId", "").strip()
        db_mid = mil_pub_map.get(zid)
        if not db_mid:
            continue

        status_id  = ml("MilestoneStatus", row.get("Status"))
        priority_id = ml("TaskPriority", row.get("Priority"))
        owner_email = row.get("Milestone Owner", "").strip().lower()
        owner_id = email_map.get(owner_email)

        # Completion % - calculated from tasks (will update via view later)
        # For now set based on status
        pct = 100 if row.get("Status","").lower() in ("completed","closed") else 0

        updates = []
        if status_id:
            updates.append(f"status_id = {status_id}")
        if priority_id:
            updates.append(f"priority_id = {priority_id}")
        if owner_id:
            updates.append(f"owner_id = {owner_id}")
        updates.append(f"completion_percentage = {pct}")

        if updates:
            sql = f"UPDATE milestones SET {', '.join(updates)} WHERE id = {db_mid}"
            try:
                cur.execute(sql)
            except Exception as e:
                print(f"  Milestone {zid} error: {e}")
            mil_updated += 1

    conn.commit()
    print(f"  Updated {mil_updated} milestones")

    # ── 6. FIX TIMELOGS - Add log_title ───────────────────────────────────────
    print("\n=== Fixing Timelogs ===")
    with open(os.path.join(CSV_DIR, "Timesheet.csv"), "r", encoding="utf-8-sig") as f:
        ts_rows = list(csv.DictReader(f))

    # Build a map: (project_id, user_id, date, hours) -> log_title from task/bug name
    # Update timelogs by setting log_title = task/bug name
    # First load the timesheet data
    tl_updated = 0
    
    # Load task and issue name lookups for log_title
    cur.execute("SELECT public_id, task_name FROM tasks WHERE public_id LIKE 'TSK-%'")
    task_name_map = {pub[4:]: name for pub, name in cur.fetchall()}
    
    cur.execute("SELECT public_id, bug_name FROM issues WHERE public_id LIKE 'ISS-%'")
    issue_name_map = {pub[4:]: name for pub, name in cur.fetchall()}

    # Get all timelogs ordered by created_at to match with CSV
    # Strategy: update log_title based on task/issue name linked in timelog
    cur.execute("""
        UPDATE timelogs t
        LEFT JOIN tasks tk ON t.task_id = tk.id
        LEFT JOIN issues i ON t.issue_id = i.id
        SET t.log_title = COALESCE(tk.task_name, i.bug_name, 'Time Log')
        WHERE t.log_title IS NULL OR t.log_title = ''
    """)
    tl_updated = cur.rowcount
    conn.commit()
    print(f"  Updated {tl_updated} timelog titles")

    # ── 7. Update v_project_stats view ────────────────────────────────────────
    print("\n=== Verifying Stats Views ===")
    try:
        cur.execute("SELECT COUNT(*) FROM v_project_stats WHERE task_count > 0")
        print(f"  Projects with tasks: {cur.fetchone()[0]}")
    except Exception as e:
        print(f"  Stats view error: {e}")
        # Recreate if missing
        cur.execute("""
            CREATE OR REPLACE VIEW v_project_stats AS
            SELECT
                p.id AS project_id,
                COUNT(t.id) AS task_count,
                SUM(CASE WHEN t.status_id IN (SELECT id FROM master_lookups WHERE category='TaskStatus' AND label IN ('Completed','Closed')) THEN 1 ELSE 0 END) AS completed_task_count,
                COUNT(i.id) AS issue_count,
                SUM(CASE WHEN i.status_id IN (SELECT id FROM master_lookups WHERE category='IssueStatus' AND label IN ('Closed','Resolved','Completed')) THEN 1 ELSE 0 END) AS completed_issue_count
            FROM projects p
            LEFT JOIN tasks t ON t.project_id = p.id AND t.is_deleted = 0
            LEFT JOIN issues i ON i.project_id = p.id AND i.is_deleted = 0
            WHERE p.is_deleted = 0
            GROUP BY p.id
        """)
        conn.commit()
        print("  Recreated v_project_stats view")

    # ── 8. Update milestone completion_percentage from actual tasks ────────────
    print("\n=== Updating Milestone Completion % from Tasks ===")
    cur.execute("""
        UPDATE milestones m
        SET m.completion_percentage = (
            SELECT COALESCE(
                ROUND(
                    100.0 * SUM(CASE WHEN t.completion_percentage = 100 OR
                        t.status_id IN (SELECT id FROM master_lookups WHERE category='TaskStatus' AND label IN ('Completed','Closed'))
                        THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0)
                ), 0)
            FROM tasks t
            WHERE t.milestone_id = m.id AND t.is_deleted = 0
        )
        WHERE m.is_deleted = 0
    """)
    conn.commit()
    print(f"  Updated milestone completion percentages")

    # ── Final summary ──────────────────────────────────────────────────────────
    print("\n=== FINAL VERIFICATION ===")
    cur.execute("SELECT COUNT(*) FROM tasks WHERE start_date IS NOT NULL")
    print(f"  Tasks with start_date: {cur.fetchone()[0]}")
    cur.execute("SELECT COUNT(*) FROM tasks WHERE due_date IS NOT NULL")
    print(f"  Tasks with due_date: {cur.fetchone()[0]}")
    cur.execute("SELECT COUNT(*) FROM tasks WHERE task_list_id IS NOT NULL")
    print(f"  Tasks with task_list_id: {cur.fetchone()[0]}")
    cur.execute("SELECT COUNT(*) FROM tasks WHERE status_id IS NOT NULL")
    print(f"  Tasks with status_id: {cur.fetchone()[0]}")
    cur.execute("SELECT COUNT(*) FROM tasks WHERE priority_id IS NOT NULL")
    print(f"  Tasks with priority_id: {cur.fetchone()[0]}")
    
    cur.execute("SELECT COUNT(*) FROM issues WHERE due_date IS NOT NULL")
    print(f"  Issues with due_date: {cur.fetchone()[0]}")
    cur.execute("SELECT COUNT(*) FROM issues WHERE severity_id IS NOT NULL")
    print(f"  Issues with severity_id: {cur.fetchone()[0]}")
    cur.execute("SELECT COUNT(*) FROM issues WHERE status_id IS NOT NULL")
    print(f"  Issues with status_id: {cur.fetchone()[0]}")
    
    cur.execute("SELECT COUNT(*) FROM projects WHERE expected_start_date IS NOT NULL")
    print(f"  Projects with start_date: {cur.fetchone()[0]}")
    cur.execute("SELECT COUNT(*) FROM projects WHERE expected_end_date IS NOT NULL")
    print(f"  Projects with end_date: {cur.fetchone()[0]}")
    
    cur.execute("SELECT COUNT(*) FROM timelogs WHERE log_title IS NOT NULL AND log_title != ''")
    print(f"  Timelogs with title: {cur.fetchone()[0]}")
    
    cur.execute("SELECT COUNT(*) FROM milestones WHERE status_id IS NOT NULL")
    print(f"  Milestones with status: {cur.fetchone()[0]}")
    cur.execute("SELECT COUNT(*) FROM milestones WHERE owner_id IS NOT NULL")
    print(f"  Milestones with owner: {cur.fetchone()[0]}")

    # Sample checks
    cur.execute("""
        SELECT t.task_name, ml_s.label as status, ml_p.label as priority, 
               t.start_date, t.due_date, tl.name as tasklist
        FROM tasks t
        LEFT JOIN master_lookups ml_s ON ml_s.id = t.status_id
        LEFT JOIN master_lookups ml_p ON ml_p.id = t.priority_id
        LEFT JOIN task_lists tl ON tl.id = t.task_list_id
        WHERE t.is_deleted=0 LIMIT 5
    """)
    print("\nSample tasks after fix:")
    for r in cur.fetchall():
        print(" ", r)

    cur.execute("""
        SELECT i.bug_name, ml_s.label as status, ml_p.label as priority, ml_sv.label as severity, i.due_date
        FROM issues i
        LEFT JOIN master_lookups ml_s ON ml_s.id = i.status_id
        LEFT JOIN master_lookups ml_p ON ml_p.id = i.priority_id
        LEFT JOIN master_lookups ml_sv ON ml_sv.id = i.severity_id
        WHERE i.is_deleted=0 LIMIT 5
    """)
    print("\nSample issues after fix:")
    for r in cur.fetchall():
        print(" ", r)

    conn.close()
    print("\nAll fields fixed successfully!")



# =========================================
# SOURCE: import_timelogs.py
# =========================================
"""
import_timelogs.py - Import timelogs from Timesheet.csv using SQL JOINs
Avoids loading all tasks/issues into Python memory by using DB-side lookups.
"""
import sys, os, csv
from datetime import datetime

sys.path.append(os.path.abspath('.'))


CSV_DIR = "C:/Users/trucs/Downloads/60018503582_portaldata_147182000003471023"
BATCH = 200

def get_conn():
    return pymysql.connect(
        host=os.getenv("DB_SERVER"),
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        db=os.getenv("DB_NAME"),
        charset="utf8mb4",
        ssl={"ssl_disabled": False},
        autocommit=False,
        read_timeout=900,
        write_timeout=900,
    )

def esc(v):
    if v is None or str(v).strip() in ("", "-", "None"):
        return "NULL"
    v = str(v).replace("\\", "\\\\").replace("'", "\\'").replace("\0", "")
    return "'" + v + "'"

def parse_date(s):
    if not s or str(s).strip() in ("", "-"):
        return None
    for fmt in ("%m/%d/%Y %I:%M %p", "%m/%d/%Y", "%Y-%m-%d %H:%M:%S",
                "%m-%d-%Y", "%Y-%m-%dT%H:%M:%S", "%m-%d-%Y %I:%M %p"):
        try:
            return datetime.strptime(s.strip(), fmt)
        except:
            pass
    return None

def run_step_4_import_timelogs():
    conn = get_conn()
    cur = conn.cursor()
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    # Only load small lookup tables: users and project IDs
    print("Loading users...")
    cur.execute("SELECT email, id FROM users WHERE email IS NOT NULL")
    email_map = {r[0].lower(): r[1] for r in cur.fetchall()}
    print(f"  Loaded {len(email_map)} users")

    print("Loading project map...")
    cur.execute("SELECT public_id, id FROM projects WHERE public_id LIKE 'PRJ-%'")
    proj_map = {pub[4:]: db_id for pub, db_id in cur.fetchall()}
    print(f"  Loaded {len(proj_map)} projects")

    # Load Timesheet CSV
    print("Reading Timesheet.csv...")
    with open(os.path.join(CSV_DIR, "Timesheet.csv"), "r", encoding="utf-8-sig") as f:
        ts_rows = list(csv.DictReader(f))
    print(f"  Total rows: {len(ts_rows)}")

    # Collect unique zoho task IDs and issue IDs we need
    needed_task_ids = set()
    needed_issue_ids = set()
    for row in ts_rows:
        type_val = row.get("Type", "").strip().lower()
        tb_id = row.get("Task/Bug Id", "").strip()
        if not tb_id:
            continue
        if type_val == "task":
            needed_task_ids.add(tb_id)
        elif type_val in ("issue", "bug"):
            needed_issue_ids.add(tb_id)

    print(f"  Need to resolve {len(needed_task_ids)} task IDs, {len(needed_issue_ids)} issue IDs")

    # Fetch only needed task IDs
    task_map = {}
    if needed_task_ids:
        task_pub_ids = ["'TSK-" + zid + "'" for zid in needed_task_ids]
        # Fetch in chunks of 1000
        chunk_size = 1000
        task_list = list(needed_task_ids)
        for i in range(0, len(task_list), chunk_size):
            chunk = task_list[i:i+chunk_size]
            placeholders = ",".join(["'TSK-" + zid + "'" for zid in chunk])
            cur.execute(f"SELECT public_id, id FROM tasks WHERE public_id IN ({placeholders})")
            for pub, db_id in cur.fetchall():
                task_map[pub[4:]] = db_id
        print(f"  Resolved {len(task_map)} task IDs")

    # Fetch only needed issue IDs
    issue_map = {}
    if needed_issue_ids:
        issue_list = list(needed_issue_ids)
        for i in range(0, len(issue_list), chunk_size):
            chunk = issue_list[i:i+chunk_size]
            placeholders = ",".join(["'ISS-" + zid + "'" for zid in chunk])
            cur.execute(f"SELECT public_id, id FROM issues WHERE public_id IN ({placeholders})")
            for pub, db_id in cur.fetchall():
                issue_map[pub[4:]] = db_id
        print(f"  Resolved {len(issue_map)} issue IDs")

    # Build insert rows
    print("Building insert rows...")
    insert_rows = []
    skipped = 0

    for row in ts_rows:
        zpid = row.get("Project Id", "").strip()
        db_pid = proj_map.get(zpid)
        if not db_pid:
            skipped += 1
            continue

        email = row.get("EmailId", "").strip().lower()
        uid = email_map.get(email)
        if not uid:
            skipped += 1
            continue

        d = parse_date(row.get("Date", ""))
        if not d:
            skipped += 1
            continue

        log_date = d.strftime("%Y-%m-%d")
        type_val = row.get("Type", "").strip().lower()
        tb_id = row.get("Task/Bug Id", "").strip()

        tid = task_map.get(tb_id) if type_val == "task" else None
        iid = issue_map.get(tb_id) if type_val in ("issue", "bug") else None

        hours_raw = row.get("Time Log", "0").strip()
        if not hours_raw or hours_raw == "-":
            hours_raw = "0"

        notes_raw = strip_html(row.get("Notes", ""))
        if notes_raw == "-":
            notes_raw = ""

        billing_type = row.get("Billing Status", "Billable").strip()
        if billing_type not in ("Billable", "Non Billable"):
            billing_type = "Billable"

        insert_rows.append((
            str(db_pid),
            str(tid) if tid else "NULL",
            str(iid) if iid else "NULL",
            str(uid),
            esc(hours_raw),
            "'" + log_date + "'",
            esc(notes_raw),
            esc(billing_type),
            now,
        ))

    print(f"  Ready to insert {len(insert_rows)} rows (skipped {skipped})")

    # Bulk insert
    cols = ("project_id, task_id, issue_id, user_id, daily_log_hours, date, notes, "
            "billing_type, general_log, is_processed, is_active, is_deleted, created_at")

    inserted = 0
    errors = 0
    for i in range(0, len(insert_rows), BATCH):
        chunk = insert_rows[i:i+BATCH]
        vals = ", ".join(
            f"({r[0]}, {r[1]}, {r[2]}, {r[3]}, {r[4]}, {r[5]}, {r[6]}, {r[7]}, 0, 0, 1, 0, '{now}')"
            for r in chunk
        )
        try:
            cur.execute(f"INSERT IGNORE INTO timelogs ({cols}) VALUES {vals}")
            conn.commit()
            inserted += len(chunk)
            if inserted % 5000 == 0 or inserted == len(insert_rows):
                print(f"  Inserted {inserted}/{len(insert_rows)}")
        except Exception as e:
            errors += 1
            conn.rollback()
            print(f"  Batch error at {i}: {e}")

    print(f"\nDone! Inserted {inserted} timelogs, {errors} batch errors, {skipped} skipped rows")

    # Final counts
    print("\n=== Final DB Counts ===")
    for t in ["projects", "milestones", "task_lists", "tasks", "issues", "timelogs", "project_members"]:
        cur.execute(f"SELECT COUNT(*) FROM {t}")
        print(f"  {t}: {cur.fetchone()[0]}")

    conn.close()



# =========================================
# SOURCE: fix_tasks_complete.py
# =========================================
"""
fix_tasks_complete.py
Re-imports all tasks correctly using the right column names from Task.csv.
Fixes: public_id, task_list_id, status, priority, dates, work_hours, completion_percentage.
Also clears incorrect default severity/priority from issues.
"""
import sys, os, csv, uuid
from datetime import datetime

sys.path.append(os.path.abspath('.'))

CSV_DIR = "C:/Users/trucs/Downloads/60018503582_portaldata_147182000003471023"
BATCH = 100

def get_conn():
    return pymysql.connect(
        host=os.getenv("DB_SERVER"), port=int(os.getenv("DB_PORT", "3306")),
        user=os.getenv("DB_USER"), password=os.getenv("DB_PASSWORD"), db=os.getenv("DB_NAME"),
        charset="utf8mb4", ssl={"ssl_disabled": False}, autocommit=False,
        read_timeout=900, write_timeout=900,
    )

def parse_date(s):
    if not s or str(s).strip() in ("", "-", "None"):
        return None
    for fmt in ("%m/%d/%Y %I:%M %p", "%m/%d/%Y", "%Y-%m-%d %H:%M:%S",
                "%m-%d-%Y %I:%M %p", "%m-%d-%Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s.strip(), fmt)
        except:
            pass
    return None

def ds(s):
    d = parse_date(s)
    return "'" + d.strftime("%Y-%m-%d") + "'" if d else "NULL"

def esc(v):
    if v is None or str(v).strip() in ("", "-", "None"):
        return "NULL"
    v = str(v).replace("\\", "\\\\").replace("'", "\\'").replace("\0", "")
    return "'" + v + "'"

def run_step_5_fix_tasks_complete():
    conn = get_conn()
    cur = conn.cursor()
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    # ── Load lookups ──────────────────────────────────────────────────────────
    print("Loading lookups...")
    cur.execute("SELECT id, category, label FROM master_lookups")
    master = {}
    for mid, cat, lbl in cur.fetchall():
        master.setdefault(cat, {})[lbl.lower()] = mid

    def ml(cat, lbl, default=None):
        if not lbl or lbl.strip().lower() in ("", "-", "none"):
            return default
        return master.get(cat, {}).get(lbl.strip().lower(), default)

    cur.execute("SELECT email, id FROM users WHERE email IS NOT NULL")
    email_map = {r[0].lower(): r[1] for r in cur.fetchall()}

    cur.execute("SELECT public_id, id FROM projects WHERE public_id LIKE 'PRJ-%'")
    proj_map = {pub[4:]: db_id for pub, db_id in cur.fetchall()}

    cur.execute("SELECT public_id, id FROM milestones WHERE public_id LIKE 'MIL-%'")
    mil_map = {pub[4:]: db_id for pub, db_id in cur.fetchall()}

    # ── Build task_list lookup: (project_id, name) -> db_id ──────────────────
    cur.execute("SELECT id, name, project_id FROM task_lists")
    tl_by_proj_name = {}
    tl_by_id = {}
    for tl_id, tl_name, tl_proj in cur.fetchall():
        tl_by_proj_name[(tl_proj, tl_name.lower())] = tl_id

    # Also rebuild z_tlid from TaskList.csv by re-reading and matching by name+project
    print("Building task_list Zoho ID map...")
    z_tlid = {}
    with open(os.path.join(CSV_DIR, "TaskList.csv"), "r", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            ztlid = row.get("Task ListId", "").strip()
            zpid  = row.get("Associated ProjectId", "").strip()
            name  = row.get("Task List", "").strip().lower()
            db_pid = proj_map.get(zpid)
            if db_pid:
                db_tlid = tl_by_proj_name.get((db_pid, name))
                if db_tlid:
                    z_tlid[ztlid] = db_tlid
    print(f"  Mapped {len(z_tlid)} task lists by Zoho ID")

    # ── Truncate tasks and related tables ─────────────────────────────────────
    print("\nTruncating tasks and related tables...")
    cur.execute("SET FOREIGN_KEY_CHECKS = 0")
    for t in ["task_assignees", "task_owners", "timelogs", "tasks"]:
        cur.execute(f"TRUNCATE TABLE {t}")
    cur.execute("SET FOREIGN_KEY_CHECKS = 1")
    conn.commit()
    print("  Truncated.")

    # ── Re-import tasks ───────────────────────────────────────────────────────
    print("\nImporting Tasks from CSV with correct column names...")
    with open(os.path.join(CSV_DIR, "Task.csv"), "r", encoding="utf-8-sig") as f:
        task_rows = list(csv.DictReader(f))
    print(f"  Total CSV rows: {len(task_rows)}")

    seen_task = {}
    insert_batches = []
    zoho_id_to_db = {}  # will fill after insert

    cols = [
        "public_id", "task_name", "description", "project_id", "task_list_id",
        "milestone_id", "created_by_id", "assignee_id", "owner_id",
        "status_id", "priority_id", "start_date", "due_date", "completion_date",
        "completion_percentage", "work_hours", "billing_type", "tags",
        "is_processed", "is_active", "is_deleted", "created_at"
    ]

    task_zids_in_order = []
    task_names_in_order = []

    for row in task_rows:
        zid  = row.get("TaskId", "").strip()  # CORRECT column name
        zpid = row.get("Associated ProjectId", "").strip()
        db_pid = proj_map.get(zpid)
        if not db_pid:
            continue

        # Name deduplication
        name = strip_html(row.get("Task", "")).strip()
        if len(name) > 200:
            name = name[:200] + "..."
        key = (db_pid, name)
        if key in seen_task:
            name = (name + f" (Z-{zid})")[:255]
        seen_task[key] = True

        # Dates
        start_date = ds(row.get("Start Date"))
        end_date   = ds(row.get("End Date"))
        completed_on = ds(row.get("Completed On"))

        # Status
        status_id = ml("TaskStatus", row.get("Status"))

        # Priority
        priority_id = ml("TaskPriority", row.get("Priority"))

        # Work hours
        try:
            bh  = float(row.get("Billable Hours", "0") or "0")
            nbh = float(row.get("Non Billable Hours", "0") or "0")
            work_hours = round(bh + nbh, 2)
        except:
            work_hours = 0

        # Completion %
        try:
            pct = int(float(row.get("Percentage Complete", "0") or "0"))
        except:
            pct = 0

        # Task list
        ztlid = row.get("Associated Task ListId", "").strip()
        db_tlid = z_tlid.get(ztlid)

        # Milestone
        zmilid = row.get("Associated MilestoneId", "").strip()
        db_milid = mil_map.get(zmilid)

        # Owner / assignee
        owner_raw = row.get("Owner", "").strip()
        owner_emails = [e.strip().lower() for e in owner_raw.split(",") if e.strip()]
        first_owner_id = email_map.get(owner_emails[0]) if owner_emails else None

        creator_email = row.get("Created by", "").strip().lower()
        creator_id = email_map.get(creator_email)

        # Billing
        billable = row.get("Billable Type", "Billable").strip()
        if billable not in ("Billable", "Non-Billable", "Internal"):
            billable = "Billable"

        # Tags
        tags_raw = row.get("Tags", "").strip()
        if tags_raw == "-":
            tags_raw = ""

        row_vals = (
            esc(f"TSK-{zid}"),                           # public_id
            esc(name),                                    # task_name
            esc(strip_html(row.get("Description", ""))),  # description
            str(db_pid),                                  # project_id
            str(db_tlid) if db_tlid else "NULL",          # task_list_id
            str(db_milid) if db_milid else "NULL",        # milestone_id
            str(creator_id) if creator_id else "NULL",    # created_by_id
            str(first_owner_id) if first_owner_id else "NULL",  # assignee_id
            str(first_owner_id) if first_owner_id else "NULL",  # owner_id
            str(status_id) if status_id else "NULL",      # status_id
            str(priority_id) if priority_id else "NULL",  # priority_id
            start_date,                                   # start_date
            end_date,                                     # due_date
            completed_on,                                 # completion_date
            str(pct),                                     # completion_percentage
            str(work_hours),                              # work_hours
            esc(billable),                                # billing_type
            esc(tags_raw) if tags_raw else "NULL",        # tags
            "0",                                          # is_processed
            "1",                                          # is_active
            "0",                                          # is_deleted
            f"'{now}'",                                   # created_at
        )
        insert_batches.append(row_vals)
        task_zids_in_order.append(zid)
        task_names_in_order.append(name)

    print(f"  Prepared {len(insert_batches)} rows to insert")

    col_str = ", ".join(cols)
    inserted = 0
    errors = 0
    for i in range(0, len(insert_batches), BATCH):
        chunk = insert_batches[i:i+BATCH]
        vals = ", ".join(f"({', '.join(r)})" for r in chunk)
        try:
            cur.execute(f"INSERT INTO tasks ({col_str}) VALUES {vals}")
            conn.commit()
            inserted += len(chunk)
            if inserted % 1000 == 0:
                print(f"  Tasks inserted: {inserted}/{len(insert_batches)}")
        except Exception as e:
            errors += 1
            conn.rollback()
            print(f"  Batch error at {i}: {e}")
            # Fall back to row-by-row for this batch
            for r in chunk:
                try:
                    cur.execute(f"INSERT INTO tasks ({col_str}) VALUES ({', '.join(r)})")
                    inserted += 1
                except Exception as e2:
                    print(f"  Row error: {e2}")
            conn.commit()

    print(f"  Inserted {inserted} tasks ({errors} batch errors)")

    # ── Re-import timelogs ────────────────────────────────────────────────────
    print("\nRe-importing Timelogs...")
    # Load task and issue ID maps
    cur.execute("SELECT public_id, id FROM tasks WHERE public_id LIKE 'TSK-%'")
    task_pub_map = {pub[4:]: db_id for pub, db_id in cur.fetchall()}

    cur.execute("SELECT public_id, id FROM issues WHERE public_id LIKE 'ISS-%'")
    issue_pub_map = {pub[4:]: db_id for pub, db_id in cur.fetchall()}

    with open(os.path.join(CSV_DIR, "Timesheet.csv"), "r", encoding="utf-8-sig") as f:
        ts_rows = list(csv.DictReader(f))

    tl_cols = [
        "project_id", "task_id", "issue_id", "user_id",
        "daily_log_hours", "date", "notes", "billing_type",
        "general_log", "is_processed", "is_active", "is_deleted", "created_at"
    ]
    tl_batches = []
    tl_skipped = 0

    for row in ts_rows:
        zpid = row.get("Project Id", "").strip()
        db_pid = proj_map.get(zpid)
        if not db_pid:
            tl_skipped += 1
            continue
        email = row.get("EmailId", "").strip().lower()
        uid = email_map.get(email)
        if not uid:
            tl_skipped += 1
            continue
        d = parse_date(row.get("Date", ""))
        if not d:
            tl_skipped += 1
            continue
        log_date = d.strftime("%Y-%m-%d")
        type_val = row.get("Type", "").strip().lower()
        tb_id = row.get("Task/Bug Id", "").strip()
        tid = task_pub_map.get(tb_id) if type_val == "task" else None
        iid = issue_pub_map.get(tb_id) if type_val in ("issue", "bug") else None
        hours = row.get("Time Log", "0").strip()
        if not hours or hours == "-": hours = "0"
        notes = strip_html(row.get("Notes", ""))
        if notes == "-": notes = ""
        billing = row.get("Billing Status", "Billable").strip()
        if billing not in ("Billable", "Non Billable"): billing = "Billable"

        tl_batches.append((
            str(db_pid),
            str(tid) if tid else "NULL",
            str(iid) if iid else "NULL",
            str(uid),
            esc(hours),
            f"'{log_date}'",
            esc(notes),
            esc(billing),
            "0", "0", "1", "0",
            f"'{now}'"
        ))

    tl_col_str = ", ".join(tl_cols)
    tl_inserted = 0
    for i in range(0, len(tl_batches), BATCH * 2):
        chunk = tl_batches[i:i+BATCH*2]
        vals = ", ".join(f"({', '.join(r)})" for r in chunk)
        try:
            cur.execute(f"INSERT INTO timelogs ({tl_col_str}) VALUES {vals}")
            conn.commit()
            tl_inserted += len(chunk)
            if tl_inserted % 5000 == 0:
                print(f"  Timelogs: {tl_inserted}/{len(tl_batches)}")
        except Exception as e:
            conn.rollback()
            print(f"  Timelog batch error at {i}: {e}")

    print(f"  Inserted {tl_inserted} timelogs (skipped {tl_skipped})")

    # ── Update timelog titles via SQL JOIN ────────────────────────────────────
    print("  Updating timelog log_title from task/issue names...")
    cur.execute("""
        UPDATE timelogs t
        LEFT JOIN tasks tk ON t.task_id = tk.id
        LEFT JOIN issues i ON t.issue_id = i.id
        SET t.log_title = COALESCE(tk.task_name, i.bug_name, 'Time Log')
        WHERE t.log_title IS NULL OR t.log_title = ''
    """)
    conn.commit()

    # ── Fix issue severity/priority NULL handling ─────────────────────────────
    print("\nFixing Issue default severity/priority (clearing wrong defaults)...")
    # First clear all wrong defaults - set to NULL where severity is the old wrong id=18
    cur.execute("UPDATE issues SET severity_id = NULL WHERE severity_id = 18")
    cur.execute("UPDATE issues SET priority_id = NULL WHERE priority_id = 8")
    conn.commit()
    print("  Cleared wrong default severity/priority from issues")

    # Now re-apply correct values from CSV
    print("  Re-applying correct severity/priority from Issue.csv...")
    with open(os.path.join(CSV_DIR, "Issue.csv"), "r", encoding="utf-8-sig") as f:
        issue_rows = list(csv.DictReader(f))

    cur.execute("SELECT public_id, id FROM issues WHERE public_id LIKE 'ISS-%'")
    issue_pub_map2 = {pub[4:]: db_id for pub, db_id in cur.fetchall()}

    issue_updated = 0
    for row in issue_rows:
        zid = row.get("IssueId", "").strip()
        db_iid = issue_pub_map2.get(zid)
        if not db_iid:
            continue

        sev_label = row.get("Severity", "").strip()
        if "-" in sev_label:
            sev_label = sev_label.split("-", 1)[1].strip()
        prio_label = row.get("Priority", "").strip()

        severity_id = ml("IssueSeverity", sev_label)
        priority_id = ml("TaskPriority", prio_label)

        updates = []
        updates.append(f"severity_id = {'NULL' if not severity_id else severity_id}")
        updates.append(f"priority_id = {'NULL' if not priority_id else priority_id}")

        cur.execute(f"UPDATE issues SET {', '.join(updates)} WHERE id = {db_iid}")
        issue_updated += 1
        if issue_updated % 500 == 0:
            conn.commit()

    conn.commit()
    print(f"  Updated {issue_updated} issues with correct severity/priority")

    # ── Final verification ─────────────────────────────────────────────────────
    print("\n=== FINAL VERIFICATION ===")
    cur.execute("SELECT COUNT(*) FROM tasks WHERE start_date IS NOT NULL")
    print(f"  Tasks with start_date: {cur.fetchone()[0]}")
    cur.execute("SELECT COUNT(*) FROM tasks WHERE due_date IS NOT NULL")
    print(f"  Tasks with due_date: {cur.fetchone()[0]}")
    cur.execute("SELECT COUNT(*) FROM tasks WHERE task_list_id IS NOT NULL")
    print(f"  Tasks with task_list_id: {cur.fetchone()[0]}")
    cur.execute("SELECT COUNT(*) FROM tasks WHERE status_id IS NOT NULL")
    print(f"  Tasks with status_id: {cur.fetchone()[0]}")
    cur.execute("SELECT COUNT(*) FROM tasks WHERE priority_id IS NOT NULL")
    print(f"  Tasks with priority_id: {cur.fetchone()[0]}")
    cur.execute("SELECT COUNT(*) FROM tasks WHERE work_hours > 0")
    print(f"  Tasks with work_hours > 0: {cur.fetchone()[0]}")
    cur.execute("SELECT COUNT(*) FROM timelogs")
    print(f"  Total timelogs: {cur.fetchone()[0]}")
    cur.execute("SELECT COUNT(*) FROM timelogs WHERE task_id IS NOT NULL")
    print(f"  Timelogs linked to tasks: {cur.fetchone()[0]}")

    cur.execute("""
        SELECT t.task_name, ml_s.label as status, ml_p.label as priority,
               t.start_date, t.due_date, tl.name as tasklist, t.work_hours, t.completion_percentage
        FROM tasks t
        LEFT JOIN master_lookups ml_s ON ml_s.id = t.status_id
        LEFT JOIN master_lookups ml_p ON ml_p.id = t.priority_id
        LEFT JOIN task_lists tl ON tl.id = t.task_list_id
        WHERE t.is_deleted=0 AND t.start_date IS NOT NULL LIMIT 5
    """)
    print("\nSample tasks with dates:")
    for r in cur.fetchall():
        print(" ", r)

    cur.execute("""
        SELECT t.task_name, ml_s.label as status, ml_p.label as priority,
               t.start_date, t.due_date, tl.name as tasklist
        FROM tasks t
        LEFT JOIN master_lookups ml_s ON ml_s.id = t.status_id
        LEFT JOIN master_lookups ml_p ON ml_p.id = t.priority_id
        LEFT JOIN task_lists tl ON tl.id = t.task_list_id
        WHERE t.is_deleted=0 LIMIT 5
    """)
    print("\nSample tasks (first 5):")
    for r in cur.fetchall():
        print(" ", r)

    cur.execute("""
        SELECT i.bug_name, ml_s.label as status, ml_p.label as priority,
               ml_sv.label as severity, i.due_date
        FROM issues i
        LEFT JOIN master_lookups ml_s ON ml_s.id = i.status_id
        LEFT JOIN master_lookups ml_p ON ml_p.id = i.priority_id
        LEFT JOIN master_lookups ml_sv ON ml_sv.id = i.severity_id
        WHERE i.is_deleted=0 LIMIT 5
    """)
    print("\nSample issues:")
    for r in cur.fetchall():
        print(" ", r)

    conn.close()
    print("\nAll task fields fixed successfully!")



# =========================================
# SOURCE: patch_remaining.py
# =========================================
"""
patch_remaining.py - Import milestones, task_lists, project_members, timelogs
Run after zoho_csv_migrator_v4.py completes
"""
import sys, os, csv, uuid
from datetime import datetime

sys.path.append(os.path.abspath('.'))


CSV_DIR = "C:/Users/trucs/Downloads/60018503582_portaldata_147182000003471023"

def get_conn():
    return pymysql.connect(
        host=os.getenv("DB_SERVER"),
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        db=os.getenv("DB_NAME"),
        charset="utf8mb4",
        ssl={"ssl_disabled": False},
        autocommit=False,
        read_timeout=900,
        write_timeout=900,
    )

def esc(v):
    if v is None or str(v).strip() in ("", "-", "None"):
        return "NULL"
    v = str(v).replace("\\", "\\\\").replace("'", "\\'").replace("\0", "")
    return "'" + v + "'"

def dt_esc(s):
    if not s or str(s).strip() in ("", "-"):
        return "NULL"
    for fmt in ("%m/%d/%Y %I:%M %p", "%m/%d/%Y", "%Y-%m-%d %H:%M:%S",
                "%m-%d-%Y", "%Y-%m-%dT%H:%M:%S", "%m-%d-%Y %I:%M %p"):
        try:
            d = datetime.strptime(s.strip(), fmt)
            return "'" + d.strftime("%Y-%m-%d %H:%M:%S") + "'"
        except:
            pass
    return "NULL"

def parse_date(s):
    if not s or str(s).strip() in ("", "-"):
        return None
    for fmt in ("%m/%d/%Y %I:%M %p", "%m/%d/%Y", "%Y-%m-%d %H:%M:%S",
                "%m-%d-%Y", "%Y-%m-%dT%H:%M:%S", "%m-%d-%Y %I:%M %p"):
        try:
            return datetime.strptime(s.strip(), fmt)
        except:
            pass
    return None


def run_step_6_patch_remaining():
    conn = get_conn()
    cur = conn.cursor()
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    # Load existing project IDs
    cur.execute("SELECT public_id, id FROM projects")
    proj_map = {}
    for pub, db_id in cur.fetchall():
        if pub and pub.startswith("PRJ-"):
            proj_map[pub[4:]] = db_id
    print(f"Loaded {len(proj_map)} projects")

    cur.execute("SELECT public_id, id FROM tasks")
    task_map = {}
    for pub, db_id in cur.fetchall():
        if pub and pub.startswith("TSK-"):
            task_map[pub[4:]] = db_id
    print(f"Loaded {len(task_map)} tasks")

    cur.execute("SELECT public_id, id FROM issues")
    issue_map = {}
    for pub, db_id in cur.fetchall():
        if pub and pub.startswith("ISS-"):
            issue_map[pub[4:]] = db_id
    print(f"Loaded {len(issue_map)} issues")

    cur.execute("SELECT email, id FROM users WHERE email IS NOT NULL")
    email_map = {r[0].lower(): r[1] for r in cur.fetchall()}
    print(f"Loaded {len(email_map)} users")

    # ── 1. Milestones (skip if already imported) ─────────────────────────────
    cur.execute("SELECT public_id, id FROM milestones")
    z_mid = {pub[4:]: db_id for pub, db_id in cur.fetchall() if pub and pub.startswith('MIL-')}
    if not z_mid:
        print("Importing Milestones...")
        with open(os.path.join(CSV_DIR, "Milestone.csv"), "r", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))

        inserted = 0
        for row in rows:
            zid  = row.get("MilestoneId", "").strip()
            zpid = row.get("Associated ProjectId", "").strip()
            db_pid = proj_map.get(zpid)
            if not db_pid:
                continue
            name = row.get("Milestone", "").strip()[:255]
            sql = (
                "INSERT IGNORE INTO milestones "
                "(public_id, milestone_name, project_id, start_date, end_date, is_active, is_deleted, created_at) "
                "VALUES ("
                + esc("MIL-" + zid) + ", "
                + esc(name) + ", "
                + str(db_pid) + ", "
                + dt_esc(row.get("Start Date")) + ", "
                + dt_esc(row.get("End Date")) + ", "
                + "1, 0, '" + now + "')"
            )
            cur.execute(sql)
            mid = cur.lastrowid
            if mid:
                z_mid[zid] = mid
                inserted += 1
            if inserted % 100 == 0:
                conn.commit()
        conn.commit()
        cur.execute("SELECT public_id, id FROM milestones")
        z_mid = {pub[4:]: db_id for pub, db_id in cur.fetchall() if pub and pub.startswith('MIL-')}
        print(f"Imported {inserted} milestones")
    else:
        print(f"Milestones already imported: {len(z_mid)}, skipping.")

    # ── 2. Task Lists (skip if already imported) ─────────────────────────────
    cur.execute("SELECT COUNT(*) FROM task_lists")
    tl_count = cur.fetchone()[0]
    if tl_count == 0:
        print("Importing Task Lists...")
        with open(os.path.join(CSV_DIR, "TaskList.csv"), "r", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))

        inserted = 0
        for row in rows:
            zid  = row.get("Task ListId", "").strip()
            zpid = row.get("Associated ProjectId", "").strip()
            zmid = row.get("Associated MilestoneId", "").strip()
            db_pid = proj_map.get(zpid)
            if not db_pid:
                continue
            db_mid_val = z_mid.get(zmid)
            name = row.get("Task List", "").strip()[:255]
            sql = (
                "INSERT IGNORE INTO task_lists "
                "(name, project_id, milestone_id, is_active, is_deleted, created_at) "
                "VALUES ("
                + esc(name) + ", "
                + str(db_pid) + ", "
                + (str(db_mid_val) if db_mid_val else "NULL") + ", "
                + "1, 0, '" + now + "')"
            )
            cur.execute(sql)
            tlid = cur.lastrowid
            if tlid:
                inserted += 1
            if inserted % 100 == 0:
                conn.commit()
        conn.commit()
        print(f"Imported {inserted} task lists")
    else:
        print(f"Task lists already imported: {tl_count}, skipping.")

    # ── 3. Project Members ────────────────────────────────────────────────────
    print("Updating Project Members from task & issue assignees...")
    cur.execute("SELECT DISTINCT project_id, assignee_id FROM tasks WHERE assignee_id IS NOT NULL")
    pm_pairs = set(cur.fetchall())
    cur.execute("SELECT DISTINCT project_id, created_by_id FROM tasks WHERE created_by_id IS NOT NULL")
    pm_pairs.update(cur.fetchall())
    cur.execute("SELECT DISTINCT project_id, reporter_id FROM issues WHERE reporter_id IS NOT NULL")
    pm_pairs.update(cur.fetchall())
    cur.execute("SELECT DISTINCT project_id, assignee_id FROM issues WHERE assignee_id IS NOT NULL")
    pm_pairs.update(cur.fetchall())

    added = 0
    for db_pid, uid in pm_pairs:
        sql = (
            "INSERT IGNORE INTO project_members (project_id, user_id, project_profile, portal_profile) "
            "VALUES (" + str(db_pid) + ", " + str(uid) + ", 'Member', 'User')"
        )
        cur.execute(sql)
        added += 1
        if added % 500 == 0:
            conn.commit()
    conn.commit()
    print(f"Added {added} project member entries")

    # ── 4. Timelogs ───────────────────────────────────────────────────────────
    print("Importing Timelogs...")
    with open(os.path.join(CSV_DIR, "Timesheet.csv"), "r", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    inserted = 0
    skipped = 0
    for i, row in enumerate(rows):
        zpid = row.get("Project Id", "").strip()
        db_pid = proj_map.get(zpid)
        if not db_pid:
            skipped += 1
            continue

        email = row.get("EmailId", "").strip().lower()
        uid = email_map.get(email)
        if not uid:
            skipped += 1
            continue

        d = parse_date(row.get("Date", ""))
        if not d:
            skipped += 1
            continue

        log_date = d.strftime("%Y-%m-%d")
        type_val = row.get("Type", "").strip().lower()
        tb_id = row.get("Task/Bug Id", "").strip()

        tid = task_map.get(tb_id) if type_val == "task" else None
        iid = issue_map.get(tb_id) if type_val in ("issue", "bug") else None

        hours_raw = row.get("Time Log", "0").strip()
        if not hours_raw or hours_raw == "-":
            hours_raw = "0"

        notes_raw = strip_html(row.get("Notes", ""))
        if notes_raw == "-":
            notes_raw = ""

        billing_type = row.get("Billing Status", "Billable").strip()
        if billing_type not in ("Billable", "Non Billable"):
            billing_type = "Billable"

        sql = (
            "INSERT IGNORE INTO timelogs "
            "(project_id, task_id, issue_id, user_id, daily_log_hours, date, notes, "
            "billing_type, general_log, is_processed, is_active, is_deleted, created_at) "
            "VALUES ("
            + str(db_pid) + ", "
            + (str(tid) if tid else "NULL") + ", "
            + (str(iid) if iid else "NULL") + ", "
            + str(uid) + ", "
            + esc(hours_raw) + ", "
            + "'" + log_date + "', "
            + esc(notes_raw) + ", "
            + esc(billing_type) + ", "
            + "0, 0, 1, 0, '" + now + "')"
        )
        cur.execute(sql)
        inserted += 1

        if inserted % 500 == 0:
            conn.commit()
            print(f"  Timelogs: {inserted}/{len(rows)} (skipped: {skipped})")

    conn.commit()
    print(f"Imported {inserted} timelogs (skipped {skipped})")

    # ── Final counts ──────────────────────────────────────────────────────────
    print("\n=== Final DB Counts ===")
    for t in ["projects", "milestones", "task_lists", "tasks", "issues", "timelogs", "project_members"]:
        cur.execute("SELECT COUNT(*) FROM " + t)
        print(f"  {t}: {cur.fetchone()[0]}")

    conn.close()
    print("\nDone!")




# =========================================
# SOURCE: fix_stats.py
# =========================================
import sys, os, pymysql
conn = pymysql.connect(host=os.getenv('DB_SERVER'), port=int(os.getenv('DB_PORT','3306')), user=os.getenv('DB_USER'), password=os.getenv('DB_PASSWORD'), db=os.getenv('DB_NAME'), charset='utf8mb4', ssl={'ssl_disabled': False})
cur = conn.cursor()

# Get the ID for 'Medium' priority
cur.execute("SELECT id FROM master_lookups WHERE category='TaskPriority' AND label='Medium'")
medium_priority = cur.fetchone()[0]
print(f'Medium priority id: {medium_priority}')

# Get 'Normal' or 'Medium' for Issue severity if needed (issues have severity and priority)
cur.execute("SELECT id FROM master_lookups WHERE category='IssueSeverity' AND label='Medium'")
medium_severity = cur.fetchone()[0]
print(f'Medium severity id: {medium_severity}')

print('Updating Tasks priority...')
cur.execute(f'UPDATE tasks SET priority_id = {medium_priority} WHERE priority_id IS NULL')
print(f'  Updated {cur.rowcount} tasks')

print('Updating Issues priority and severity...')
cur.execute(f'UPDATE issues SET priority_id = {medium_priority} WHERE priority_id IS NULL')
print(f'  Updated {cur.rowcount} issue priorities')
cur.execute(f'UPDATE issues SET severity_id = {medium_severity} WHERE severity_id IS NULL')
print(f'  Updated {cur.rowcount} issue severities')

print('Updating Projects priority...')
cur.execute(f'UPDATE projects SET priority_id = {medium_priority} WHERE priority_id IS NULL')
print(f'  Updated {cur.rowcount} projects')

print('Updating Milestones priority...')
cur.execute(f'UPDATE milestones SET priority_id = {medium_priority} WHERE priority_id IS NULL')
print(f'  Updated {cur.rowcount} milestones')

# Recreate v_project_stats view
print('Recreating v_project_stats...')
cur.execute("""
    CREATE OR REPLACE VIEW v_project_stats AS
    SELECT
        p.id AS project_id,
        COUNT(DISTINCT t.id) AS task_count,
        COUNT(DISTINCT CASE WHEN t.status_id IN (SELECT id FROM master_lookups WHERE category='TaskStatus' AND label IN ('Completed','Closed')) THEN t.id END) AS completed_task_count,
        COUNT(DISTINCT i.id) AS issue_count,
        COUNT(DISTINCT m.id) AS milestone_count
    FROM projects p
    LEFT JOIN tasks t ON p.id = t.project_id AND t.is_deleted = 0
    LEFT JOIN issues i ON p.id = i.project_id AND i.is_deleted = 0
    LEFT JOIN milestones m ON p.id = m.project_id AND m.is_deleted = 0
    WHERE p.is_deleted = 0
    GROUP BY p.id
""")

# Update milestone completion percentages
print('Updating milestone completion_percentage...')
cur.execute("""
    UPDATE milestones m
    SET m.completion_percentage = (
        SELECT COALESCE(
            ROUND(
                100.0 * SUM(CASE WHEN t.status_id IN (SELECT id FROM master_lookups WHERE category='TaskStatus' AND label IN ('Completed','Closed')) THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0)
            ), 0)
        FROM tasks t
        WHERE t.milestone_id = m.id AND t.is_deleted = 0
    )
    WHERE m.is_deleted = 0
""")
print(f'  Updated {cur.rowcount} milestones completion %')

conn.commit()
conn.close()


# =========================================
# SOURCE: update_sp.py
# =========================================
import os, pymysql

conn = pymysql.connect(
    host=os.getenv('DB_SERVER'),
    port=int(os.getenv('DB_PORT','3306')),
    user=os.getenv('DB_USER'),
    password=os.getenv('DB_PASSWORD'),
    db=os.getenv('DB_NAME'),
    charset='utf8mb4',
    ssl={'ssl_disabled': False}
)
cur = conn.cursor()

sp_sql = """
CREATE PROCEDURE sp_get_projects(
    IN p_skip INT,
    IN p_limit INT,
    IN p_status_ids JSON,
    IN p_priority_ids JSON,
    IN p_manager_emails JSON,
    IN p_member_email VARCHAR(255),
    IN p_is_archived BOOLEAN,
    IN p_is_template BOOLEAN,
    IN p_search VARCHAR(255),
    IN p_user_id INT,
    IN p_view_level VARCHAR(10)
)
BEGIN
    SELECT 
        p.id, p.public_id, p.project_id_sync, p.account_name, p.project_name, p.customer_name, p.client_name,
        p.tags, p.billing_model, p.project_type, p.project_status_external, p.expected_start_date, p.expected_end_date,
        p.description, p.status_id, p.priority_id, p.owner_id, p.project_manager_id, p.delivery_head_id, p.template_id,
        p.estimated_hours, p.actual_hours, p.actual_start_date, p.actual_end_date, p.is_archived, p.is_template, p.is_group,
        p.is_processed, p.previous_status_id, p.created_at, p.updated_at,
        
        (SELECT JSON_OBJECT('id', sm.id, 'label', sm.label, 'value', sm.value, 'color', sm.color, 'category', sm.category) 
         FROM master_lookups sm WHERE sm.id = p.status_id) AS status_master_json,
         
        (SELECT JSON_OBJECT('id', pm.id, 'label', pm.label, 'value', pm.value, 'color', pm.color, 'category', pm.category) 
         FROM master_lookups pm WHERE pm.id = p.priority_id) AS priority_master_json,
         
        (SELECT JSON_OBJECT('id', u.id, 'email', u.email, 'first_name', u.first_name, 'last_name', u.last_name) 
         FROM users u WHERE u.id = p.owner_id) AS owner_json,
         
        (SELECT JSON_OBJECT('id', u.id, 'email', u.email, 'first_name', u.first_name, 'last_name', u.last_name) 
         FROM users u WHERE u.id = p.project_manager_id) AS project_manager_json,
         
        (SELECT JSON_OBJECT('id', u.id, 'email', u.email, 'first_name', u.first_name, 'last_name', u.last_name) 
         FROM users u WHERE u.id = p.delivery_head_id) AS delivery_head_json,

        (SELECT COALESCE(JSON_ARRAYAGG(JSON_OBJECT('project_id', pmemb.project_id, 'user_id', pmemb.user_id, 'project_profile', pmemb.project_profile, 'portal_profile', pmemb.portal_profile, 'role_in_project', pmemb.role_in_project, 'user', JSON_OBJECT('id', u.id, 'email', u.email, 'first_name', u.first_name, 'last_name', u.last_name))), '[]')
         FROM project_members pmemb JOIN users u ON pmemb.user_id = u.id WHERE pmemb.project_id = p.id) AS team_members_json,
         
        (SELECT COUNT(t.id) FROM tasks t WHERE t.project_id = p.id AND t.is_deleted = 0) AS task_count,
        (SELECT COUNT(t.id) FROM tasks t WHERE t.project_id = p.id AND t.status_id IN (SELECT id FROM master_lookups WHERE category='TaskStatus' AND label IN ('Completed','Closed')) AND t.is_deleted = 0) AS completed_task_count,
        (SELECT COUNT(i.id) FROM issues i WHERE i.project_id = p.id AND i.is_deleted = 0) AS issue_count,
        (SELECT COUNT(m.id) FROM milestones m WHERE m.project_id = p.id) AS milestone_count

    FROM projects p
    WHERE p.is_deleted = 0
      AND (p_status_ids IS NULL OR JSON_CONTAINS(p_status_ids, CAST(p.status_id AS CHAR)))
      AND (p_priority_ids IS NULL OR JSON_CONTAINS(p_priority_ids, CAST(p.priority_id AS CHAR)))
      AND (p_is_archived IS NULL OR p.is_archived = p_is_archived)
      AND (p_is_template IS NULL OR p.is_template = p_is_template)
      AND (p_search IS NULL OR p.project_name LIKE CONCAT('%', p_search, '%') OR p.public_id LIKE CONCAT('%', p_search, '%') OR p.project_id_sync LIKE CONCAT('%', p_search, '%') OR p.customer_name LIKE CONCAT('%', p_search, '%') OR p.client_name LIKE CONCAT('%', p_search, '%'))
      AND (
          p_manager_emails IS NULL 
          OR EXISTS (SELECT 1 FROM users u WHERE (u.id = p.project_manager_id OR u.id = p.owner_id) AND JSON_CONTAINS(p_manager_emails, CONCAT('"', u.email, '"')))
      )
      AND (
          p_member_email IS NULL
          OR EXISTS (SELECT 1 FROM project_members pmemb JOIN users u ON pmemb.user_id = u.id WHERE pmemb.project_id = p.id AND u.email = p_member_email)
      )
      AND (
          p_view_level IS NULL OR p_user_id IS NULL OR p_view_level != 'O' OR p.owner_id = p_user_id
      )
      AND (
          p_view_level IS NULL OR p_user_id IS NULL OR p_view_level != 'A' OR p.id IN (
              SELECT project_id FROM project_members WHERE user_id = p_user_id
              UNION
              SELECT id FROM projects WHERE owner_id = p_user_id
              UNION
              SELECT project_id FROM tasks WHERE (assignee_id = p_user_id OR created_by_id = p_user_id) AND project_id IS NOT NULL
              UNION
              SELECT project_id FROM issues WHERE (assignee_id = p_user_id OR reporter_id = p_user_id) AND project_id IS NOT NULL
          )
      )
    ORDER BY p.created_at DESC
    LIMIT p_limit OFFSET p_skip;
END
"""

cur.execute("DROP PROCEDURE IF EXISTS sp_get_projects")
cur.execute(sp_sql)

print("Updated sp_get_projects")
conn.commit()
conn.close()


# =========================================
# SOURCE: patch_sps.py
# =========================================
import asyncio
from app.core.database import SessionLocal
from sqlalchemy import text

SP_PROJECTS = """
CREATE PROCEDURE `sp_get_projects_count`(
    IN p_status_ids JSON,
    IN p_priority_ids JSON,
    IN p_manager_emails JSON,
    IN p_member_email VARCHAR(255),
    IN p_is_archived BOOLEAN,
    IN p_is_template BOOLEAN,
    IN p_search VARCHAR(255),
    IN p_user_id INT,
    IN p_view_level VARCHAR(10)
)
BEGIN
    SELECT 
        COUNT(DISTINCT p.id) AS total,
        SUM(IF(ml.label NOT IN ('Completed', 'Closed'), 1, 0)) AS active,
        SUM(IF(ml.label IN ('Completed', 'Closed'), 1, 0)) AS completed,
        SUM(IF(ml.label = 'Planning', 1, 0)) AS planning
    FROM projects p
    LEFT JOIN master_lookups ml ON p.status_id = ml.id
    WHERE p.is_deleted = 0
      AND (p_status_ids IS NULL OR JSON_CONTAINS(p_status_ids, CAST(p.status_id AS CHAR)))
      AND (p_priority_ids IS NULL OR JSON_CONTAINS(p_priority_ids, CAST(p.priority_id AS CHAR)))
      AND (p_is_archived IS NULL OR p.is_archived = p_is_archived)
      AND (p_is_template IS NULL OR p.is_template = p_is_template)
      AND (p_search IS NULL OR p.project_name LIKE CONCAT('%', p_search, '%') OR p.public_id LIKE CONCAT('%', p_search, '%') OR p.project_id_sync LIKE CONCAT('%', p_search, '%') OR p.customer_name LIKE CONCAT('%', p_search, '%') OR p.client_name LIKE CONCAT('%', p_search, '%'))
      AND (
          p_manager_emails IS NULL 
          OR EXISTS (SELECT 1 FROM users u WHERE (u.id = p.project_manager_id OR u.id = p.owner_id) AND JSON_CONTAINS(p_manager_emails, CONCAT('"', u.email, '"')))
      )
      AND (
          p_member_email IS NULL
          OR EXISTS (SELECT 1 FROM project_members pmemb JOIN users u ON pmemb.user_id = u.id WHERE pmemb.project_id = p.id AND u.email = p_member_email)
      )
      AND (
          p_view_level IS NULL OR p_user_id IS NULL OR p_view_level != 'O' OR p.owner_id = p_user_id
      )
      AND (
          p_view_level IS NULL OR p_user_id IS NULL OR p_view_level != 'A' OR p.id IN (
              SELECT project_id FROM project_members WHERE user_id = p_user_id
              UNION
              SELECT id FROM projects WHERE owner_id = p_user_id
              UNION
              SELECT project_id FROM tasks WHERE (assignee_id = p_user_id OR created_by_id = p_user_id) AND project_id IS NOT NULL
              UNION
              SELECT project_id FROM issues WHERE (assignee_id = p_user_id OR reporter_id = p_user_id) AND project_id IS NOT NULL
          )
      );
END
"""

SP_TASKS = """
CREATE PROCEDURE `sp_get_tasks_count`(
    IN p_project_id INT,
    IN p_status_ids JSON,
    IN p_priority_ids JSON,
    IN p_assignee_emails JSON,
    IN p_milestone_id INT,
    IN p_search VARCHAR(255),
    IN p_overdue_only BOOLEAN
)
BEGIN
    SELECT 
        COUNT(DISTINCT t.id) AS total,
        SUM(IF(ml.label NOT IN ('Completed', 'Closed'), 1, 0)) AS active,
        SUM(IF(ml.label IN ('Completed', 'Closed'), 1, 0)) AS completed
    FROM tasks t
    LEFT JOIN master_lookups ml ON t.status_id = ml.id
    WHERE t.is_deleted = 0
      AND (p_project_id IS NULL OR t.project_id = p_project_id)
      AND (p_milestone_id IS NULL OR t.milestone_id = p_milestone_id)
      AND (p_status_ids IS NULL OR JSON_CONTAINS(p_status_ids, CAST(t.status_id AS CHAR)))
      AND (p_priority_ids IS NULL OR JSON_CONTAINS(p_priority_ids, CAST(t.priority_id AS CHAR)))
      AND (p_search IS NULL OR t.task_name LIKE CONCAT('%', p_search, '%') OR t.public_id LIKE CONCAT('%', p_search, '%'))
      AND (p_overdue_only = 0 OR t.due_date < CURDATE())
      AND (
          p_assignee_emails IS NULL 
          OR EXISTS (SELECT 1 FROM users u WHERE u.id = t.assignee_id AND JSON_CONTAINS(p_assignee_emails, CONCAT('"', u.email, '"')))
          OR EXISTS (SELECT 1 FROM task_assignees ta JOIN users u ON ta.user_id = u.id WHERE ta.task_id = t.id AND JSON_CONTAINS(p_assignee_emails, CONCAT('"', u.email, '"')))
          OR EXISTS (SELECT 1 FROM task_owners tow JOIN users u ON tow.user_id = u.id WHERE tow.task_id = t.id AND JSON_CONTAINS(p_assignee_emails, CONCAT('"', u.email, '"')))
      );
END
"""

SP_ISSUES = """
CREATE PROCEDURE `sp_get_issues_count`(
    IN p_project_id INT,
    IN p_status_ids JSON,
    IN p_priority_ids JSON,
    IN p_severity_ids JSON,
    IN p_assignee_emails JSON,
    IN p_milestone_id INT,
    IN p_search VARCHAR(255)
)
BEGIN
    SELECT 
        COUNT(DISTINCT i.id) AS total,
        SUM(IF(ml.label NOT IN ('Completed', 'Closed', 'Resolved'), 1, 0)) AS active,
        SUM(IF(ml.label IN ('Completed', 'Closed', 'Resolved'), 1, 0)) AS completed
    FROM issues i
    LEFT JOIN master_lookups ml ON i.status_id = ml.id
    WHERE i.is_deleted = 0
      AND (p_project_id IS NULL OR i.project_id = p_project_id)
      AND (p_milestone_id IS NULL OR i.milestone_id = p_milestone_id)
      AND (p_status_ids IS NULL OR JSON_CONTAINS(p_status_ids, CAST(i.status_id AS CHAR)))
      AND (p_priority_ids IS NULL OR JSON_CONTAINS(p_priority_ids, CAST(i.priority_id AS CHAR)))
      AND (p_severity_ids IS NULL OR JSON_CONTAINS(p_severity_ids, CAST(i.severity_id AS CHAR)))
      AND (p_search IS NULL OR i.bug_name LIKE CONCAT('%', p_search, '%') OR i.public_id LIKE CONCAT('%', p_search, '%'))
      AND (
          p_assignee_emails IS NULL 
          OR EXISTS (SELECT 1 FROM users u WHERE u.id = i.assignee_id AND JSON_CONTAINS(p_assignee_emails, CONCAT('"', u.email, '"')))
          OR EXISTS (SELECT 1 FROM issue_assignees ia JOIN users u ON ia.user_id = u.id WHERE ia.issue_id = i.id AND JSON_CONTAINS(p_assignee_emails, CONCAT('"', u.email, '"')))
          OR EXISTS (SELECT 1 FROM issue_followers f JOIN users u ON f.user_id = u.id WHERE f.issue_id = i.id AND JSON_CONTAINS(p_assignee_emails, CONCAT('"', u.email, '"')))
      );
END
"""

def run_patch():
    db = SessionLocal()
    try:
        db.execute(text("DROP PROCEDURE IF EXISTS sp_get_projects_count"))
        db.execute(text(SP_PROJECTS))
        print("Patched sp_get_projects_count")
        
        db.execute(text("DROP PROCEDURE IF EXISTS sp_get_tasks_count"))
        db.execute(text(SP_TASKS))
        print("Patched sp_get_tasks_count")
        
        db.execute(text("DROP PROCEDURE IF EXISTS sp_get_issues_count"))
        db.execute(text(SP_ISSUES))
        print("Patched sp_get_issues_count")
        
        db.commit()
        print("All stored procedures updated successfully.")
    except Exception as e:
        db.rollback()
        print("Error:", e)
    finally:
        db.close()



# =========================================
# STEP 10: update_milestone_stats
# =========================================
def run_step_10_update_milestone_stats():
    import os, pymysql

    conn = pymysql.connect(
        host=os.getenv('DB_SERVER'),
        port=int(os.getenv('DB_PORT','3306')),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
        db=os.getenv('DB_NAME'),
        charset='utf8mb4',
        ssl={'ssl_disabled': False}
    )
    cur = conn.cursor()

    print('Recreating v_milestone_stats...')
    cur.execute("""
        CREATE OR REPLACE VIEW v_milestone_stats AS
        SELECT
            m.id AS milestone_id,
            COUNT(DISTINCT t.id) AS task_count,
            COUNT(DISTINCT CASE WHEN t.status_id IN (SELECT id FROM master_lookups WHERE category='TaskStatus' AND label IN ('Completed','Closed')) THEN t.id END) AS completed_task_count,
            COUNT(DISTINCT i.id) AS issue_count
        FROM milestones m
        LEFT JOIN tasks t ON m.id = t.milestone_id AND t.is_deleted = 0
        LEFT JOIN issues i ON m.id = i.milestone_id AND i.is_deleted = 0
        GROUP BY m.id
    """)

    print("Updated v_milestone_stats")
    conn.commit()
    conn.close()


# =========================================
# STEP 11: fix_project_managers
# =========================================
def run_step_11_fix_project_managers():
    import os, pymysql

    conn = pymysql.connect(
        host=os.getenv('DB_SERVER'),
        port=int(os.getenv('DB_PORT','3306')),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
        db=os.getenv('DB_NAME'),
        ssl={'ssl_disabled': False},
        charset='utf8mb4',
        autocommit=True
    )
    cursor = conn.cursor()
    cursor.execute("UPDATE projects SET project_manager_id = owner_id WHERE project_manager_id IS NULL AND owner_id IS NOT NULL")
    print("Updated project_manager_id from owner_id. Affected rows:", cursor.rowcount)
    cursor.close()
    conn.close()


# =========================================
# STEP 12: import_attachments & Azure Blob Upload
# =========================================
def run_step_12_import_attachments():
    import os, csv, pymysql, urllib.request, io
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from app.services.azure_blob_service import azure_blob_service

    conn = pymysql.connect(
        host=os.getenv('DB_SERVER'),
        port=int(os.getenv('DB_PORT', '3306')),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
        db=os.getenv('DB_NAME'),
        ssl={'ssl_disabled': False},
        charset='utf8mb4',
        autocommit=True
    )
    cursor = conn.cursor()

    print("Loading issue map...")
    cursor.execute("SELECT public_id, id, project_id FROM issues WHERE public_id IS NOT NULL AND public_id != ''")
    issue_map = {}
    for r in cursor.fetchall():
        pub_id = r[0]
        issue_map[pub_id] = (r[1], r[2])
        if pub_id.startswith('ISS-'):
            issue_map[pub_id[4:]] = (r[1], r[2])

    print("Loading task map...")
    cursor.execute("SELECT public_id, id, project_id FROM tasks WHERE public_id IS NOT NULL AND public_id != ''")
    task_map = {}
    for r in cursor.fetchall():
        pub_id = r[0]
        task_map[pub_id] = (r[1], r[2])
        if pub_id.startswith('TSK-'):
            task_map[pub_id[4:]] = (r[1], r[2])

    print("Loading project map...")
    cursor.execute("SELECT public_id, id FROM projects WHERE public_id IS NOT NULL AND public_id != ''")
    proj_map = {}
    for r in cursor.fetchall():
        pub_id = r[0]
        proj_map[pub_id] = r[1]
        if pub_id.startswith('PRJ-'):
            proj_map[pub_id[4:]] = r[1]

    cursor.execute("SELECT id FROM projects")
    for r in cursor.fetchall():
        proj_map[str(r[0])] = r[0]

    print("Loading existing Azure Blob URLs...")
    cursor.execute("SELECT public_id, file_url FROM documents WHERE public_id IS NOT NULL AND (file_url LIKE '%blob.core.windows.net%' OR file_url LIKE '%azure%')")
    existing_azure_urls = dict(cursor.fetchall())
    print(f"Found {len(existing_azure_urls)} attachments already in Azure Blob Storage.")

    csv_path = r'C:\Users\trucs\Downloads\60018503582_portaldata_147182000003471023\AttachmentMapping.csv'
    if not os.path.exists(csv_path):
        print("AttachmentMapping.csv not found! Skipping attachment migration.")
        conn.close()
        return

    print("Processing attachments from CSV...")
    items_to_process = []
    with open(csv_path, encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            att_id = row['AttachmentId']
            entity_id = row['EntityId']
            name = row['AttachmentName']
            size = row['AttachmentSize'] or '0'
            c_type = row['ContentType'] or 'application/octet-stream'
            url = row['AttachmentUrlOrPath'] or ''

            issue_info = issue_map.get(entity_id)
            task_info = task_map.get(entity_id)
            proj_id = proj_map.get(entity_id)

            project_id = None
            issue_id = None
            if issue_info:
                issue_id, project_id = issue_info
            elif task_info:
                _, project_id = task_info
            elif proj_id:
                project_id = proj_id

            if not project_id or not url:
                continue

            try:
                size_int = int(size)
            except:
                size_int = 0

            items_to_process.append({
                'att_id': att_id,
                'name': name[:255],
                'url': url[:1024],
                'c_type': c_type[:100],
                'size': size_int,
                'project_id': project_id,
                'issue_id': issue_id
            })

    print(f"Total attachment rows to process: {len(items_to_process)}")

    cursor.close()
    conn.close()
    print("Closed database connection during upload phase.")

    def download_and_upload(item):
        if item['att_id'] in existing_azure_urls:
            return item['att_id'], existing_azure_urls[item['att_id']]
        zoho_url = item['url']
        if not zoho_url.startswith('http'):
            return item['att_id'], zoho_url
        try:
            req = urllib.request.Request(zoho_url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data_bytes = resp.read()
            file_stream = io.BytesIO(data_bytes)
            blob_name = azure_blob_service.upload_file(file_stream, item['name'], item['c_type'])
            return item['att_id'], blob_name or zoho_url
        except Exception as e:
            print(f"Failed to migrate attachment {item['att_id']} ({item['name']}): {e}")
            return item['att_id'], zoho_url

    print("Downloading from Zoho and uploading to Azure Blob Storage (using 20 worker threads)...")
    url_map = {}
    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = {executor.submit(download_and_upload, item): item for item in items_to_process}
        completed_count = 0
        for future in as_completed(futures):
            att_id, final_url = future.result()
            url_map[att_id] = final_url
            completed_count += 1
            if completed_count % 200 == 0 or completed_count == len(items_to_process):
                print(f"Migrated attachments to Azure: {completed_count} / {len(items_to_process)}")

    def get_fresh_conn():
        return pymysql.connect(
            host=os.getenv('DB_SERVER'),
            port=int(os.getenv('DB_PORT', '3306')),
            user=os.getenv('DB_USER'),
            password=os.getenv('DB_PASSWORD'),
            db=os.getenv('DB_NAME'),
            ssl={'ssl_disabled': False},
            charset='utf8mb4',
            autocommit=True
        )

    conn = get_fresh_conn()
    cursor = conn.cursor()

    docs_batch = []
    links_batch = []
    for item in items_to_process:
        final_url = url_map.get(item['att_id'], item['url'])
        docs_batch.append((item['att_id'], item['name'], final_url[:1024], item['c_type'], item['size'], item['project_id']))
        if item['issue_id']:
            links_batch.append((item['issue_id'], item['att_id']))

    print(f"Inserting {len(docs_batch)} documents into database...")
    chunk_size = 500
    docs_inserted = 0
    for i in range(0, len(docs_batch), chunk_size):
        chunk = docs_batch[i:i+chunk_size]
        try:
            conn.ping(reconnect=True)
        except:
            conn = get_fresh_conn()
            cursor = conn.cursor()
        cursor.executemany("""
            INSERT INTO documents (public_id, title, file_url, file_type, file_size, project_id, created_at, is_active, is_deleted)
            VALUES (%s, %s, %s, %s, %s, %s, NOW(), 1, 0)
            ON DUPLICATE KEY UPDATE title=VALUES(title), file_url=VALUES(file_url), project_id=VALUES(project_id)
        """, chunk)
        docs_inserted += len(chunk)
        print(f"Inserted documents: {docs_inserted} / {len(docs_batch)}")

    print("Creating issue-document links...")
    try:
        conn.ping(reconnect=True)
    except:
        conn = get_fresh_conn()
        cursor = conn.cursor()
    cursor.execute("SELECT public_id, id FROM documents WHERE public_id IS NOT NULL")
    doc_id_map = dict(cursor.fetchall())

    link_tuples = []
    for issue_id, att_id in links_batch:
        doc_id = doc_id_map.get(att_id)
        if doc_id:
            link_tuples.append((issue_id, doc_id))

    for i in range(0, len(link_tuples), chunk_size):
        chunk = link_tuples[i:i+chunk_size]
        try:
            conn.ping(reconnect=True)
        except:
            conn = get_fresh_conn()
            cursor = conn.cursor()
        cursor.executemany("""
            INSERT IGNORE INTO issue_document_link (issue_id, document_id, created_at, is_active, is_deleted)
            VALUES (%s, %s, NOW(), 1, 0)
        """, chunk)
        print(f"Linked issues: {min(i+chunk_size, len(link_tuples))} / {len(link_tuples)}")

    cursor.close()
    conn.close()
    print("Step 12: Attachment migration complete!")


# =========================================
# EXECUTION
# =========================================

# =========================================
# SOURCE: import_users.py
# =========================================
def run_step_00_import_users():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT email FROM users")
    existing_emails = {r[0].lower() for r in cur.fetchall()}

    def process_file(filename, is_external):
        inserted = 0
        filepath = os.path.join(CSV_DIR, filename)
        if not os.path.exists(filepath):
            print(f"Skipping {filename} - not found.")
            return

        with open(filepath, "r", encoding="utf-8-sig") as f:
            import csv
            for row in csv.DictReader(f):
                email = row.get("Email Address", "").strip()
                if not email or email.lower() in existing_emails:
                    continue

                user_id = row.get("User Id", "").strip()
                name = strip_html(row.get("User Name", "").strip())
                
                parts = name.split(" ", 1)
                first_name = parts[0]
                last_name = parts[1] if len(parts) > 1 else ""

                profile = row.get("Profile", "").strip().lower()
                role_id = 4 # Employee
                if "admin" in profile:
                    role_id = 1
                elif "manager" in profile:
                    role_id = 3
                elif "lead" in profile:
                    role_id = 2
                
                if is_external:
                    role_id = 4
                
                status_id = 1 # Active
                if row.get("Is Active", "true").lower() == "false":
                    status_id = 2 # Inactive

                # username from email
                username = email.split('@')[0]
                
                sql = f"""
                INSERT INTO users (
                    public_id, employee_id, first_name, last_name, email, username,
                    is_external, role_id, status_id, is_active, is_deleted
                ) VALUES (
                    {esc(user_id)}, {esc('EMP-' + user_id)}, {esc(first_name)}, {esc(last_name)}, {esc(email)}, {esc(username)},
                    {1 if is_external else 0}, {role_id}, {status_id}, {1 if status_id==1 else 0}, 0
                )
                """
                try:
                    cur.execute(sql)
                    existing_emails.add(email.lower())
                    inserted += 1
                except Exception as e:
                    print(f"Error inserting {email}: {e}")
        
        print(f"Inserted {inserted} users from {filename}")

    process_file("PortalUser.csv", is_external=False)
    process_file("ClientUser.csv", is_external=True)
    
    conn.commit()
    conn.close()


if __name__ == '__main__':
    print('Starting Master Zoho Migration...')
    print('\nRunning run_step_00_import_users...')
    try:
        run_step_00_import_users()
    except Exception as e:
        print(f'Error in run_step_00_import_users: {e}')

    print('\nRunning run_step_0_zoho_csv_migrator_v4...')
    try:
        run_step_0_zoho_csv_migrator_v4()
    except Exception as e:
        print(f'Error in run_step_0_zoho_csv_migrator_v4: {e}')
    print('\nRunning run_step_1_clean_duplicates...')
    try:
        run_step_1_clean_duplicates()
    except Exception as e:
        print(f'Error in run_step_1_clean_duplicates: {e}')
    print('\nRunning run_step_2_fix_users...')
    try:
        run_step_2_fix_users()
    except Exception as e:
        print(f'Error in run_step_2_fix_users: {e}')
    print('\nRunning run_step_3_fix_all_fields...')
    try:
        run_step_3_fix_all_fields()
    except Exception as e:
        print(f'Error in run_step_3_fix_all_fields: {e}')
    print('\nRunning run_step_4_import_timelogs...')
    try:
        run_step_4_import_timelogs()
    except Exception as e:
        print(f'Error in run_step_4_import_timelogs: {e}')
    print('\nRunning run_step_5_fix_tasks_complete...')
    try:
        run_step_5_fix_tasks_complete()
    except Exception as e:
        print(f'Error in run_step_5_fix_tasks_complete: {e}')
    print('\nRunning run_step_6_patch_remaining...')
    try:
        run_step_6_patch_remaining()
    except Exception as e:
        print(f'Error in run_step_6_patch_remaining: {e}')
    print('\nRunning run_step_7_fix_stats...')
    try:
        pass # run_step_7_fix_stats()
    except Exception as e:
        print(f'Error in run_step_7_fix_stats: {e}')
    print('\nRunning run_step_8_update_sp...')
    try:
        pass # run_step_8_update_sp()
    except Exception as e:
        print(f'Error in run_step_8_update_sp: {e}')
    print('\nRunning run_step_9_patch_sps...')
    try:
        pass # run_step_9_patch_sps()
    except Exception as e:
        print(f'Error in run_step_9_patch_sps: {e}')
    print('\nRunning run_step_10_update_milestone_stats...')
    try:
        run_step_10_update_milestone_stats()
    except Exception as e:
        print(f'Error in run_step_10_update_milestone_stats: {e}')
    print('\nRunning run_step_11_fix_project_managers...')
    try:
        run_step_11_fix_project_managers()
    except Exception as e:
        print(f'Error in run_step_11_fix_project_managers: {e}')
    print('\nRunning run_step_12_import_attachments...')
    try:
        pass # run_step_12_import_attachments()  # Skipped as per request
    except Exception as e:
        print(f'Error in run_step_12_import_attachments: {e}')
    print('\nAll migration steps completed!')
