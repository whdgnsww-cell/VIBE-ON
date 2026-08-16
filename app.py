import os, sqlite3, secrets, csv, io
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, session, flash, Response, abort
from werkzeug.security import check_password_hash, generate_password_hash
import qrcode

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get('SOLOPARTY_DB', os.path.join(BASE, 'party.db'))
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'change-me-now')
ADMIN_HASH = generate_password_hash(ADMIN_PASSWORD)

PHASES = ['checkin','profile','vote','closed']
PHASE_LABEL = {'checkin':'입장/체크인','profile':'프로필 작성','vote':'투표 진행','closed':'마감'}


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS events(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      code TEXT UNIQUE NOT NULL,
      name TEXT NOT NULL,
      event_date TEXT,
      phase TEXT NOT NULL DEFAULT 'checkin',
      max_votes INTEGER NOT NULL DEFAULT 3,
      created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS participants(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      event_id INTEGER NOT NULL,
      token TEXT UNIQUE NOT NULL,
      participant_no TEXT NOT NULL,
      nickname TEXT NOT NULL,
      age_group TEXT,
      mbti TEXT,
      gender TEXT,
visit_source TEXT,
      intro TEXT,
      answer1 TEXT,
      answer2 TEXT,
      checked_in_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      UNIQUE(event_id, participant_no),
      UNIQUE(event_id, nickname),
      FOREIGN KEY(event_id) REFERENCES events(id)
    );
    CREATE TABLE IF NOT EXISTS votes(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      event_id INTEGER NOT NULL,
      voter_id INTEGER NOT NULL,
      target_no TEXT NOT NULL,
      rank_no INTEGER NOT NULL,
      created_at TEXT NOT NULL,
      UNIQUE(event_id, voter_id, rank_no),
      FOREIGN KEY(event_id) REFERENCES events(id),
      FOREIGN KEY(voter_id) REFERENCES participants(id)
    );
    ''')
    columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(participants)").fetchall()
    }

    if "gender" not in columns:
        conn.execute("ALTER TABLE participants ADD COLUMN gender TEXT")

    if "visit_source" not in columns:
        conn.execute("ALTER TABLE participants ADD COLUMN visit_source TEXT")

    conn.commit()
    conn.close()


def now(): return datetime.now().strftime('%Y-%m-%d %H:%M:%S')

def event_by_code(code):
    conn=db(); row=conn.execute('SELECT * FROM events WHERE code=?',(code.upper(),)).fetchone(); conn.close(); return row

def participant_for(event_id):
    token=session.get(f'participant_{event_id}')
    if not token: return None
    conn=db(); p=conn.execute('SELECT * FROM participants WHERE event_id=? AND token=?',(event_id,token)).fetchone(); conn.close(); return p

def admin_required():
    if not session.get('admin'): abort(403)

@app.context_processor
def inject_globals():
    return dict(phase_label=PHASE_LABEL)

@app.route('/')
def home():
    return render_template('home.html')

@app.route('/join', methods=['POST'])
def join_code():
    code=request.form.get('code','').strip().upper()
    if not event_by_code(code):
        flash('파티 코드를 확인해주세요.')
        return redirect(url_for('home'))
    return redirect(url_for('party', code=code))

@app.route('/p/<code>', methods=['GET','POST'])
def party(code):
    ev=event_by_code(code)
    if not ev: abort(404)
    p=participant_for(ev['id'])
    if request.method=='POST' and not p:
        nickname=request.form.get('nickname','').strip()
        age_group=request.form.get('age_group','').strip()
        gender=request.form.get('gender','').strip()
        visit_source=request.form.get('visit_source','').strip()
        visit_source_other=request.form.get('visit_source_other','').strip()

        if len(nickname) < 1:
            flash('닉네임을 입력해주세요.')
            return redirect(url_for('party', code=code))

        if age_group not in ('20대', '30대'):
            flash('나이대를 선택해주세요.')
            return redirect(url_for('party', code=code))

        if gender not in ('남성', '여성'):
            flash('성별을 선택해주세요.')
            return redirect(url_for('party', code=code))

        allowed_sources = (
            '인스타그램',
            '블로그',
            '인터넷 검색',
            '모임·소개팅 앱',
            '지인 추천',
            '재방문',
            '기타'
        )

        if visit_source not in allowed_sources:
            flash('방문 경로를 선택해주세요.')
            return redirect(url_for('party', code=code))

        if visit_source == '기타':
            if not visit_source_other:
                flash('기타 방문 경로를 입력해주세요.')
                return redirect(url_for('party', code=code))
            visit_source = '기타: ' + visit_source_other

        # 기존 시스템 호환을 위해 내부 participant_no에는 닉네임을 저장합니다.
        participant_no = nickname

        conn=db()
        try:
            token=secrets.token_urlsafe(24)

            conn.execute(
                '''INSERT INTO participants(
                    event_id,
                    token,
                    participant_no,
                    nickname,
                    age_group,
                    gender,
                    visit_source,
                    checked_in_at,
                    updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?)''',
                (
                    ev['id'],
                    token,
                    participant_no,
                    nickname,
                    age_group,
                    gender,
                    visit_source,
                    now(),
                    now()
                )
            )

            conn.commit()
            session[f'participant_{ev["id"]}']=token

        except sqlite3.IntegrityError:
            flash('이미 사용 중인 닉네임입니다. 다른 닉네임을 사용해주세요.')

        finally:
            conn.close()

        return redirect(url_for('party', code=code))
    return render_template('party.html', ev=ev, p=p)

@app.route('/p/<code>/profile', methods=['POST'])
def save_profile(code):
    ev=event_by_code(code)
    p=participant_for(ev['id']) if ev else None

    if not ev or not p:
        abort(403)

    if ev['phase'] not in ('profile','vote'):
        flash('지금은 프로필 작성 시간이 아닙니다.')
        return redirect(url_for('party', code=code))

    vals=[
        request.form.get(k,'').strip()
        for k in ['mbti','intro','answer1','answer2']
    ]

    conn=db()
    conn.execute(
        '''UPDATE participants
           SET mbti=?, intro=?, answer1=?, answer2=?, updated_at=?
           WHERE id=?''',
        (*vals, now(), p['id'])
    )
    conn.commit()
    conn.close()

    flash('작성 내용이 저장되었습니다. 다른 참가자에게 공개되지 않습니다.')
    return redirect(url_for('party', code=code))

@app.route('/p/<code>/vote', methods=['POST'])
def save_vote(code):
    ev=event_by_code(code); p=participant_for(ev['id']) if ev else None
    if not ev or not p: abort(403)
    if ev['phase']!='vote':
        flash('관리자가 아직 투표를 열지 않았습니다.')
        return redirect(url_for('party',code=code))
    targets=[]
    for i in range(1, ev['max_votes']+1):
        t=request.form.get(f'vote{i}','').strip().upper()
        if t: targets.append((i,t))
    if len({t for _,t in targets}) != len(targets):
        flash('같은 번호를 중복 선택할 수 없습니다.'); return redirect(url_for('party',code=code))
    if any(t==p['participant_no'] for _,t in targets):
        flash('본인에게는 투표할 수 없습니다.'); return redirect(url_for('party',code=code))
    conn=db()
    valid=set(r['participant_no'] for r in conn.execute('SELECT participant_no FROM participants WHERE event_id=?',(ev['id'],)))
    if any(t not in valid for _,t in targets):
        conn.close(); flash('존재하지 않는 명찰 번호가 포함되어 있습니다.'); return redirect(url_for('party',code=code))
    conn.execute('DELETE FROM votes WHERE event_id=? AND voter_id=?',(ev['id'],p['id']))
    for rank,t in targets:
        conn.execute('INSERT INTO votes(event_id,voter_id,target_no,rank_no,created_at) VALUES(?,?,?,?,?)',(ev['id'],p['id'],t,rank,now()))
    conn.commit(); conn.close(); flash('투표가 저장되었습니다. 결과는 관리자만 확인할 수 있습니다.')
    return redirect(url_for('party',code=code))

@app.route('/admin', methods=['GET','POST'])
def admin_login():
    if request.method=='POST':
        if check_password_hash(ADMIN_HASH, request.form.get('password','')):
            session['admin']=True; return redirect(url_for('admin_dashboard'))
        flash('관리자 비밀번호가 올바르지 않습니다.')
    if session.get('admin'): return redirect(url_for('admin_dashboard'))
    return render_template('admin_login.html')

@app.route('/admin/logout')
def admin_logout(): session.pop('admin',None); return redirect(url_for('admin_login'))

@app.route('/admin/dashboard', methods=['GET','POST'])
def admin_dashboard():
    admin_required(); conn=db()
    if request.method=='POST':
        name=request.form.get('name','').strip() or '솔로파티'
        code=request.form.get('code','').strip().upper() or secrets.token_hex(3).upper()
        date=request.form.get('event_date','').strip()
        max_votes=max(1,min(5,int(request.form.get('max_votes','3'))))
        try:
            conn.execute('INSERT INTO events(code,name,event_date,phase,max_votes,created_at) VALUES(?,?,?,?,?,?)',(code,name,date,'checkin',max_votes,now())); conn.commit()
        except sqlite3.IntegrityError: flash('이미 사용 중인 파티 코드입니다.')
    events=conn.execute('''SELECT e.*, (SELECT COUNT(*) FROM participants p WHERE p.event_id=e.id) participants,
                           (SELECT COUNT(*) FROM votes v WHERE v.event_id=e.id) votes
                           FROM events e ORDER BY e.id DESC''').fetchall(); conn.close()
    return render_template('admin_dashboard.html',events=events)

@app.route('/admin/event/<int:event_id>', methods=['GET','POST'])
def admin_event(event_id):
    admin_required(); conn=db(); ev=conn.execute('SELECT * FROM events WHERE id=?',(event_id,)).fetchone()
    if not ev: conn.close(); abort(404)
    if request.method=='POST':
        phase=request.form.get('phase')
        if phase in PHASES:
            conn.execute('UPDATE events SET phase=? WHERE id=?',(phase,event_id)); conn.commit(); ev=conn.execute('SELECT * FROM events WHERE id=?',(event_id,)).fetchone()
    participants=conn.execute('SELECT * FROM participants WHERE event_id=? ORDER BY id',(event_id,)).fetchall()
    votes=conn.execute('''SELECT v.*, p.nickname voter_nickname, p.participant_no voter_no,
                         tp.nickname target_nickname FROM votes v
                         JOIN participants p ON p.id=v.voter_id
                         LEFT JOIN participants tp ON tp.event_id=v.event_id AND tp.participant_no=v.target_no
                         WHERE v.event_id=? ORDER BY v.voter_id,v.rank_no''',(event_id,)).fetchall()
    ranking=conn.execute('''SELECT tp.participant_no,tp.nickname,
           SUM(CASE v.rank_no WHEN 1 THEN 3 WHEN 2 THEN 2 ELSE 1 END) score,
           COUNT(v.id) vote_count
           FROM participants tp LEFT JOIN votes v ON v.event_id=tp.event_id AND v.target_no=tp.participant_no
           WHERE tp.event_id=? GROUP BY tp.id ORDER BY score DESC, vote_count DESC, tp.id''',(event_id,)).fetchall()
    # mutual: any vote in either direction, regardless of rank
    mutual=conn.execute('''SELECT DISTINCT a.participant_no a_no,a.nickname a_name,b.participant_no b_no,b.nickname b_name
      FROM participants a JOIN participants b ON a.event_id=b.event_id AND a.id<b.id
      WHERE a.event_id=? AND EXISTS(SELECT 1 FROM votes v1 WHERE v1.event_id=a.event_id AND v1.voter_id=a.id AND v1.target_no=b.participant_no)
      AND EXISTS(SELECT 1 FROM votes v2 WHERE v2.event_id=a.event_id AND v2.voter_id=b.id AND v2.target_no=a.participant_no)
      ORDER BY a.id,b.id''',(event_id,)).fetchall()
    conn.close()
    return render_template('admin_event.html',ev=ev,participants=participants,votes=votes,ranking=ranking,mutual=mutual)

@app.route('/admin/event/<int:event_id>/qr.png')
def event_qr(event_id):
    admin_required(); conn=db(); ev=conn.execute('SELECT * FROM events WHERE id=?',(event_id,)).fetchone(); conn.close()
    if not ev: abort(404)
    url=request.url_root.rstrip('/') + url_for('party',code=ev['code'])
    img=qrcode.make(url); buf=io.BytesIO(); img.save(buf,'PNG'); buf.seek(0)
    return Response(buf.getvalue(), mimetype='image/png')

@app.route('/admin/event/<int:event_id>/export.csv')
def export_csv(event_id):
    admin_required(); conn=db(); ev=conn.execute('SELECT * FROM events WHERE id=?',(event_id,)).fetchone()
    if not ev: conn.close(); abort(404)
    rows=conn.execute('''SELECT p.participant_no,p.nickname,p.age_group,p.mbti,p.intro,p.answer1,p.answer2,
      GROUP_CONCAT(CASE WHEN v.rank_no=1 THEN v.target_no END) vote1,
      GROUP_CONCAT(CASE WHEN v.rank_no=2 THEN v.target_no END) vote2,
      GROUP_CONCAT(CASE WHEN v.rank_no=3 THEN v.target_no END) vote3
      FROM participants p LEFT JOIN votes v ON v.voter_id=p.id WHERE p.event_id=? GROUP BY p.id ORDER BY p.id''',(event_id,)).fetchall(); conn.close()
    out=io.StringIO(); w=csv.writer(out); w.writerow(['명찰번호','닉네임','연령대','MBTI','한줄소개','질문1','질문2','1순위','2순위','3순위'])
    for r in rows: w.writerow(list(r))
    data='\ufeff'+out.getvalue()
    return Response(data, mimetype='text/csv; charset=utf-8', headers={'Content-Disposition':f'attachment; filename=party_{ev["code"]}.csv'})

init_db()
if __name__=='__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT','5000')), debug=True)
@app.route('/admin/event/<int:event_id>/reset', methods=['POST'])
def reset_event(event_id):
    admin_required()
    conn = db()

    ev = conn.execute(
        'SELECT * FROM events WHERE id=?',
        (event_id,)
    ).fetchone()

    if not ev:
        conn.close()
        abort(404)

    conn.execute(
        'DELETE FROM votes WHERE event_id=?',
        (event_id,)
    )
    conn.execute(
        'DELETE FROM participants WHERE event_id=?',
        (event_id,)
    )
    conn.execute(
        "UPDATE events SET phase='checkin' WHERE id=?",
        (event_id,)
    )

    conn.commit()
    conn.close()

    flash('파티 데이터가 초기화되었습니다.')
    return redirect(url_for('admin_event', event_id=event_id))
