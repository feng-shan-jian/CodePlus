"""Real kill-before/after-COMMIT and a separate writer during pending Milvus IO."""
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import time
import ctypes
from ctypes import wintypes
from queue import Queue,Empty
from threading import Thread
from uuid import UUID

import pytest
from agentic_rag.storage import Catalog,publication
from agentic_rag.indexes.milvus import MilvusRevisionIndex
from agentic_rag.models.identity import process_birth

H=runpy.run_path(str(Path(__file__).with_name('publication_support.py')))
pytestmark=pytest.mark.skipif(os.environ.get('R10_REAL')!='1',reason='owned real Milvus acceptance required')


@pytest.mark.parametrize('mode',['before-commit','after-commit','redirector-diagnostic'])
def test_real_publication_process_termination_and_concurrent_metadata(tmp_path,mode):
    helper=Path(__file__).with_name('publication_process_helper.py')
    actual_mode='before-commit' if mode=='redirector-diagnostic' else mode
    proc=subprocess.Popen([sys.executable,'-I','-B',str(helper),actual_mode,str(tmp_path)],stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8')
    report={'mode':mode,'scope':'real Milvus/SQLite/process kill; synthetic 1024D protocol vectors','pid':proc.pid}
    actual=None
    def terminate():
        if os.name=='nt' and actual is not None and process_birth(actual['pid'])==actual['process_birth']:
            kernel=ctypes.WinDLL('kernel32',use_last_error=True)
            kernel.OpenProcess.argtypes=[ctypes.c_uint32,ctypes.c_int,ctypes.c_uint32];kernel.OpenProcess.restype=ctypes.c_void_p
            kernel.TerminateProcess.argtypes=[ctypes.c_void_p,ctypes.c_uint32]
            kernel.WaitForSingleObject.argtypes=[ctypes.c_void_p,ctypes.c_uint32]
            kernel.CloseHandle.argtypes=[ctypes.c_void_p]
            kernel.GetProcessTimes.argtypes=[ctypes.c_void_p,*([ctypes.POINTER(wintypes.FILETIME)]*4)]
            handle=kernel.OpenProcess(0x101001,False,actual['pid']);assert handle
            try:
                created,exited,kernel_time,user_time=(wintypes.FILETIME() for _ in range(4))
                assert kernel.GetProcessTimes(handle,ctypes.byref(created),ctypes.byref(exited),
                    ctypes.byref(kernel_time),ctypes.byref(user_time))
                handle_birth=str((created.dwHighDateTime<<32)|created.dwLowDateTime)
                assert handle_birth==actual['process_birth'], 'owned interpreter identity changed'
                assert kernel.TerminateProcess(handle,91)
                assert kernel.WaitForSingleObject(handle,10000)==0
                report['actual_interpreter_termination']={'pid':actual['pid'],'process_birth':actual['process_birth'],
                    'same_handle_birth':handle_birth,'wait':'signaled'}
            finally:kernel.CloseHandle(handle)
        elif proc.poll() is None:
            if os.name=='nt':subprocess.run(['taskkill','/PID',str(proc.pid),'/T','/F'],capture_output=True,timeout=15)
            else:proc.kill()
        proc.wait(15)
    def read():
        output=Queue()
        reader=Thread(target=lambda:output.put(proc.stdout.readline()),daemon=True)
        reader.start()
        try:line=output.get(timeout=180)
        except Empty:
            terminate();reader.join(5)
            raise TimeoutError('publication child produced no gate within 180 seconds')
        reader.join(5)
        if not line:
            raise AssertionError(proc.stderr.read())
        return json.loads(line)
    try:
        first=read();assert first['gate']=='service_io';report['io_gate']=first;actual=first
        started=time.perf_counter()
        other=subprocess.run([sys.executable,'-I','-B',str(helper),'other-library',str(tmp_path)],
            capture_output=True,text=True,encoding='utf-8',timeout=10)
        assert other.returncode==0,other.stderr
        report['independent_writer']=json.loads(other.stdout)
        report['independent_process_seconds']=time.perf_counter()-started
        proc.stdin.write('continue\n');proc.stdin.flush()
        gate=read();assert gate['gate']==actual_mode.replace('-','_');report['publish_gate']=gate
        if mode=='redirector-diagnostic':
            proc.kill();proc.wait(15)
            observation={'launcher_exit':proc.returncode,'actual_birth_after_launcher_exit':process_birth(actual['pid']),
                'directory_attributes':getattr(tmp_path.stat(),'st_file_attributes',None)}
            try:
                premature=Catalog(tmp_path/'data')
                observation['premature_open']='succeeded'
            except Exception as exc:
                observation.update(premature_open='failed',type=type(exc).__name__,message=str(exc),
                    result=getattr(exc,'result',None),extendedresult=getattr(exc,'extendedresult',None))
            report['redirector_only_diagnostic']=observation
        terminate();report['returncode']=proc.returncode
        assert proc.poll() is not None
        catalog=Catalog(tmp_path/'data');kb=UUID(gate['kb_id']);batch=UUID(gate['batch_id'])
        receipt=publication.receipt(catalog,batch,UUID(gate['revision_id']))
        current=catalog.get_library(kb).current_revision_id
        if actual_mode=='before-commit':
            assert receipt is None and current is None
            interrupted=catalog.identify_interrupted(kb);assert interrupted
            with catalog.resume_mutation(interrupted) as owner:owner.abandon()
        else:
            assert receipt and str(current)==gate['revision_id']
            assert catalog.get_library(kb).pending_mutation_id is None
            assert catalog.identify_interrupted(kb) is None
            assert publication.receipt(catalog,batch)==receipt
        report.update(result='PASS',receipt=receipt,current_revision_id=str(current) if current else None)
    finally:
        if proc.poll() is None or (actual is not None and process_birth(actual['pid'])==actual['process_birth']):terminate()
        report['stderr']=proc.stderr.read()
        state_path=tmp_path/'process-state.json'
        if state_path.exists():
            state=json.loads(state_path.read_text(encoding='utf-8'))
            catalog=Catalog(tmp_path/'data');backend=MilvusRevisionIndex(H['configuration'](tmp_path/'data').storage,catalog)
            artifact=publication.artifact(catalog,UUID(state['revision_id']))
            backend.client.drop_collection(backend._name(artifact));backend.close()
        if os.environ.get('R10_PROCESS_REPORT_DIR'):
            Path(os.environ['R10_PROCESS_REPORT_DIR'],f'R10-process-{mode}.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
