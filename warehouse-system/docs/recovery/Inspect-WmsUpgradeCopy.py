"""Read/inspect a verified cold copy only; never migrate or start the WMS app."""
import argparse, hashlib, json, os, re, socket, subprocess, sys, time, traceback
from pathlib import Path
import pymysql

NO_WINDOW=subprocess.CREATE_NO_WINDOW
def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def store(path,value): path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf8')
def child(root,relative):
 p=Path(relative)
 if p.is_absolute() or '..' in p.parts or ':' in str(p): raise RuntimeError('UNSAFE_RELATIVE_PATH')
 target=(root/p).resolve()
 if not target.is_relative_to(root): raise RuntimeError('OUTSIDE_COPY')
 return target
def main():
 p=argparse.ArgumentParser(); p.add_argument('--copy',type=Path,required=True); p.add_argument('--source',type=Path,required=True); p.add_argument('--port',type=int,required=True); args=p.parse_args()
 root=args.copy.resolve(); source=args.source.resolve(); out=root.parent
 if (out/'inspection.json').exists() or (out/'current-copy.sql').exists(): raise RuntimeError('USE_A_NEW_DIAGNOSTIC_RUN')
 if root==source or root.is_relative_to(source) or source.is_relative_to(root) or root.name!='working': raise RuntimeError('COPY_LOCATION_INVALID')
 if Path(sys.executable).resolve()!=root/'python/python.exe': raise RuntimeError('INTERPRETER_OUTSIDE_COPY')
 if not (out/'copy-verification.json').is_file(): raise RuntimeError('COLD_COPY_PROOF_MISSING')
 proof=json.loads((out/'copy-verification.json').read_text(encoding='utf-8-sig'))
 if proof.get('sha256_all_equal') is not True or proof.get('test_port')!=args.port: raise RuntimeError('COPY_PROOF_INVALID')
 for item in root.rglob('*'):
  if item.is_symlink() or item.is_junction(): raise RuntimeError('LINK_NOT_ALLOWED')
 cfg=json.loads((root/'data/config.json').read_text(encoding='utf8'))
 if cfg.get('format')!=1 or cfg.get('ready') is not True or not cfg.get('server_uuid'): raise RuntimeError('CONFIG_INVALID')
 if not 1024<=args.port<=65535 or args.port in (80,3306,cfg['mysql_port'],cfg['web_port']): raise RuntimeError('TEST_PORT_INVALID')
 with socket.socket() as sock: sock.bind(('127.0.0.1',args.port))
 result={'ok':False,'copy_only':True,'original_config_sha256':digest(root/'data/config.json'),'marker_preserved':True,'test_port':args.port}
 marker=root/'data/upgrade-in-progress.json'; marker_hash=digest(marker) if marker.exists() else None
 if marker.exists():
  state=json.loads(marker.read_text(encoding='utf8')); old_path=Path(state['backup'])
  if not old_path.is_absolute() or old_path.parent!=source/'data/upgrade-backups': raise RuntimeError('MARKER_BACKUP_PATH_INVALID')
  session=child(root,old_path.relative_to(source)); result['backup_session']=session.name
  result['upgrade_report_exists']=(session/'report.json').is_file()
  for name in ('before-check.log','after-check.log'):
   file=session/name
   if file.is_file():
    # These installer diagnostics omit credentials, but don't export raw logs automatically.
    result[name]={'exists':True,'sha256':digest(file)}
   else: result[name]={'exists':False}
  sql=session/'before-upgrade.sql'; sha=session/'before-upgrade.sql.sha256'
  result['before_sql_sha_pass']=sql.is_file() and sha.is_file() and digest(sql)==sha.read_text(encoding='ascii').split()[0]
  if (session/'report.json').is_file():
   prior=json.loads((session/'report.json').read_text(encoding='utf8'))
   result['prior_report']={k:prior[k] for k in ('phase','error_code','error_type','migration_started','shutdown_error','target_revision') if k in prior}
 else: session=None; prior=None; result['upgrade_report_exists']=False
 prior=locals().get('prior')
 for label,base in [('current',root),('old',session/'old' if session else None)]:
  manifest_file=base/'package-manifest.json' if base else None
  if not manifest_file or not manifest_file.is_file(): result[label+'_manifest']={'exists':False}; continue
  manifest=json.loads(manifest_file.read_text(encoding='utf8')); mismatches=[]
  for name,expected in manifest.get('files',{}).items():
   if label=='old' and Path(name).parts[0] not in ('controller','backend','frontend','轻仓WMS.exe','使用说明.txt','package-manifest.json'): check=child(root,name)
   else: check=child(base,name)
   if not check.is_file() or digest(check)!=expected: mismatches.append(name)
  result[label+'_manifest']={'exists':True,'version':manifest.get('version'),'baseline':manifest.get('business_baseline'),'mismatches':mismatches}
 process=None; con=None
 def connect():
  conn=pymysql.connect(host='127.0.0.1',port=args.port,user='root',password=cfg['root_password'],database='warehouse_standalone',autocommit=True,connect_timeout=2,charset='utf8mb4')
  try:
   with conn.cursor() as cur:
    cur.execute('SELECT @@datadir,@@port,@@server_uuid'); directory,port,identity=cur.fetchone()
   if Path(directory).resolve()!=root/'data/mysql' or port!=args.port or identity!=cfg['server_uuid']: raise RuntimeError('INSTANCE_MISMATCH')
   return conn
  except Exception: conn.close(); raise
 try:
  process=subprocess.Popen([str(root/'mysql/bin/mysqld.exe'),'--no-defaults','--basedir='+str(root/'mysql'),'--datadir='+str(root/'data/mysql'),'--port='+str(args.port),'--bind-address=127.0.0.1','--mysqlx=0','--local-infile=0','--innodb-buffer-pool-size=128M','--max-connections=20','--log-error='+str(out/'copy-mysql.log')],creationflags=NO_WINDOW)
  for _ in range(120):
   if process.poll() is not None: raise RuntimeError('COPY_MYSQL_START_FAILED')
   try: con=connect(); break
   except pymysql.Error: time.sleep(.5)
  if con is None: raise RuntimeError('COPY_MYSQL_START_TIMEOUT')
  rows={}; table_checks=[]
  with con.cursor() as cur:
   cur.execute('SELECT version_num FROM alembic_version'); result['database_revision']=[x[0] for x in cur.fetchall()]
   cur.execute("SELECT COLUMN_NAME,COLUMN_TYPE,IS_NULLABLE FROM information_schema.columns WHERE table_schema='warehouse_standalone' AND table_name='stock_documents' AND column_name='material_outbound_purpose'"); result['purpose_column']=cur.fetchall()
   cur.execute("SELECT tc.CONSTRAINT_NAME,cc.CHECK_CLAUSE,tc.ENFORCED FROM information_schema.table_constraints tc JOIN information_schema.check_constraints cc ON cc.CONSTRAINT_SCHEMA=tc.CONSTRAINT_SCHEMA AND cc.CONSTRAINT_NAME=tc.CONSTRAINT_NAME WHERE tc.CONSTRAINT_SCHEMA='warehouse_standalone' AND tc.TABLE_NAME='stock_documents' AND tc.CONSTRAINT_NAME='ck_stock_documents_valid_material_outbound_purpose'"); result['purpose_check']=cur.fetchall()
   if result['purpose_column']:
    cur.execute('SELECT COUNT(*),COUNT(material_outbound_purpose),SUM(material_outbound_purpose IN (\'supplier_return\',\'scrap\')) FROM stock_documents'); result['purpose_counts']=[int(x or 0) for x in cur.fetchone()]
   cur.execute('SHOW TABLES'); tables=[x[0] for x in cur.fetchall()]
   for table in tables:
    if not re.fullmatch(r'[A-Za-z0-9_]+',table): raise RuntimeError('UNSAFE_TABLE_NAME')
    cur.execute('CHECK TABLE `'+table+'`'); checks=cur.fetchall()
    if not all(row[2]=='status' and row[3]=='OK' for row in checks): raise RuntimeError('COPY_TABLE_CHECK_FAILED')
    table_checks.append(table)
    if table=='alembic_version': continue
    columns=prior.get('before',{}).get(table,{}).get('columns') if prior else None
    if columns:
     if not all(re.fullmatch(r'[A-Za-z0-9_]+',c) for c in columns): raise RuntimeError('UNSAFE_COLUMN_NAME')
     cur.execute('SELECT '+','.join('`'+c+'`' for c in columns)+' FROM `'+table+'`')
    else:
     cur.execute('SELECT * FROM `'+table+'`'); columns=[x[0] for x in cur.description]
    values=sorted(json.dumps(row,ensure_ascii=True,default=str,separators=(',',':')) for row in cur.fetchall())
    rows[table]={'columns':columns,'count':len(values),'sha256':hashlib.sha256('\n'.join(values).encode()).hexdigest()}
   grants=[]
   for account in ('wms_app','wms_migrator'):
    cur.execute("SHOW GRANTS FOR '"+account+"'@'localhost'"); grants.extend(sorted(x[0] for x in cur.fetchall()))
   result['grants_sha256']=hashlib.sha256('\n'.join(grants).encode()).hexdigest()
  result['table_checks_pass']=table_checks; result['row_fingerprints']=rows
  if prior:
   result['prior_old_rows_equal']=rows==prior.get('before'); result['prior_grants_equal']=result['grants_sha256']==prior.get('grants_sha256'); result['prior_config_equal']=result['original_config_sha256']==prior.get('config_sha256')
  result['structural_class']='unconfirmed'
  if result['database_revision']==['0014'] and not result['purpose_column'] and not result['purpose_check']: result['structural_class']='candidate_complete_0014'
  elif result['database_revision']==['0015'] and result['purpose_column'] and result['purpose_check']:
   # Explicitly compare the published 0015 CHECK, including parentheses/enforcement.
   expected="((material_outbound_purpose is null) or ((kind = 'material_outbound') and (material_outbound_purpose in ('normal','supplier_return','scrap')) and ((material_outbound_purpose = 'normal') or ((notes is not null) and (char_length(trim(notes)) > 0)))))"
   canonical=lambda text: re.sub(r'\s+','',text.lower().replace('`','').replace('_utf8mb4','').replace('_utf8mb3','').replace("\\'","'"))
   checks=result['purpose_check']
   result['purpose_schema_pass']=(result['purpose_column']==(('material_outbound_purpose','varchar(20)','YES'),) and len(checks)==1 and checks[0][2]=='YES' and canonical(checks[0][1])==canonical(expected))
   result['structural_class']='candidate_complete_0015' if result['purpose_schema_pass'] else 'partial_or_mismatched_STOP'
  else: result['structural_class']='partial_or_mismatched_STOP'
  # Never infer a complete schema from the two migration fields alone.
  backend=root/'backend' if result['database_revision']==['0015'] else session/'old/backend' if session else None
  if backend and (backend/'alembic.ini').is_file() and result['structural_class']!='partial_or_mismatched_STOP':
   env=os.environ.copy(); env.update(APP_ENV='production',MYSQL_HOST='127.0.0.1',MYSQL_PORT=str(args.port),MYSQL_DATABASE='warehouse_standalone',MYSQL_USER='wms_app',MYSQL_PASSWORD=cfg['app_password'],MYSQL_MIGRATION_USER='wms_migrator',MYSQL_MIGRATION_PASSWORD=cfg['migration_password'],AUTH_SECRET=cfg['auth_secret'],BUSINESS_TIMEZONE='Asia/Shanghai',CORS_ORIGINS='[]',API_DOCS_ENABLED='false',SERVE_FRONTEND='false')
   code="import sys;sys.path.insert(0,sys.argv[1]);from alembic.config import Config;from alembic import command;c=Config(sys.argv[1]+'/alembic.ini');command.current(c);command.heads(c);command.check(c);from scripts.check_inventory import main;sys.exit(main())"
   diag=subprocess.run([str(root/'python/python.exe'),'-I','-c',code,str(backend)],cwd=backend,env=env,capture_output=True,creationflags=NO_WINDOW,timeout=180)
   result['alembic_inventory_exit']=diag.returncode
   if diag.returncode==0: (out/'copy-check.log').write_bytes(diag.stdout+diag.stderr)
   else:
    error=diag.stderr.decode('utf8',errors='replace'); kinds=re.findall(r'^([A-Za-z_][\w.]*)\s*:',error,re.MULTILINE)
    result['diagnostic_error_type']=kinds[-1] if kinds else 'Unknown'
  else: result['alembic_inventory_exit']='not_run_missing_matching_backend_or_partial_structure'
  # Fresh logical backup of current COPY state; never import it.
  options=out/'copy-dump.cnf'; options.write_text('[client]\nhost=127.0.0.1\nport='+str(args.port)+'\nuser=wms_migrator\npassword='+cfg['migration_password']+'\n',encoding='utf8')
  try:
   sql=out/'current-copy.sql'
   with sql.open('xb') as stream:
    dump=subprocess.run([str(root/'mysql/bin/mysqldump.exe'),'--defaults-extra-file='+str(options),'--single-transaction','--routines','--triggers','--set-gtid-purged=OFF','warehouse_standalone'],stdout=stream,stderr=subprocess.PIPE,creationflags=NO_WINDOW)
   if dump.returncode: raise RuntimeError('COPY_DUMP_FAILED')
   sql.with_suffix('.sql.sha256').write_text(digest(sql)+'  '+sql.name+'\n',encoding='ascii')
   result['current_copy_sql_sha256']=digest(sql)
  finally: options.unlink(missing_ok=True)
  result['ok']=(result['structural_class']!='partial_or_mismatched_STOP' and result['alembic_inventory_exit']==0)
  if prior and not all(result.get(k) is True for k in ('prior_old_rows_equal','prior_grants_equal','prior_config_equal')): result['ok']=False
  if marker.exists() and result.get('before_sql_sha_pass') is not True: result['ok']=False
  result['recovery_authorized']=False
  result['data_equivalence_proven']=bool(prior and result.get('prior_old_rows_equal') is True)
 finally:
  if con is not None:
   try:
    with con.cursor() as cur: cur.execute('SHUTDOWN')
   finally: con.close()
  elif process is not None and process.poll() is None:
   # If identity could not be verified, don't stop an unknown server.
   result['shutdown_unverified_STOP']=True
  if process is not None and con is not None:
   process.wait(timeout=60)
   if process.returncode!=0: result['ok']=False; raise RuntimeError('COPY_MYSQL_SHUTDOWN_FAILED')
  if marker_hash is not None and digest(marker)!=marker_hash: result['ok']=False; raise RuntimeError('MARKER_CHANGED')
  if digest(root/'data/config.json')!=result['original_config_sha256']: result['ok']=False; raise RuntimeError('COPY_CONFIG_CHANGED')
  store(out/'inspection.json',result)
 print(json.dumps({'ok':result['ok'],'database_revision':result.get('database_revision'),'structural_class':result.get('structural_class'),'alembic_inventory_exit':result.get('alembic_inventory_exit'),'marker_preserved':True},ensure_ascii=True))
 return 0 if result['ok'] else 1
if __name__=='__main__':
 try: sys.exit(main())
 except Exception as exc:
  # Only safe exception types and frame locations; never credentials or DB rows.
  print(json.dumps({'ok':False,'exception_type':type(exc).__name__,'frames':[(Path(f.filename).name,f.lineno) for f in traceback.extract_tb(exc.__traceback__)[-4:]]}))
  sys.exit(1)
