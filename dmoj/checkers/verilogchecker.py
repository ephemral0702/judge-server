# dmoj/checkers/verilogchecker.py
import re
from dmoj.result import CheckerResult

def check(proc_out: bytes, judge_out: bytes, **kw):
    res = kw.get('result')
    case = kw.get('case')
    limit = getattr(case.config, 'output_limit_length', None)
    flag  = getattr(res, 'result_flag', None)

    text = proc_out.decode(errors='ignore')
    if not text.strip():
        fb  = f'no output on stdout (limit={limit}, flag={flag})'
        ext = f'stderr_first200={ (res.feedback or b"")[:200]!r}'
        return CheckerResult(False, 0, feedback=fb, extended_feedback=ext)

    lines = [l.strip() for l in text.splitlines() if l.strip()]
    last = lines[-1]

    if re.match(r'^RESULT:\s*OK$', last, re.I):
        return CheckerResult(True, kw.get('point_value', 0.0))

    m = re.match(r'^RESULT:\s*WA\s+(\d+)', last, re.I)
    if m:
        errs = int(m.group(1))
        return CheckerResult(False, 0.0, feedback=f'{errs} mismatch(es)')

    if re.match(r'^RESULT:\s*WA\s+TIMEOUT', last, re.I):
        return CheckerResult(False, 0.0, feedback='timeout')

    return CheckerResult(False, 0.0, feedback='format error', extended_feedback=last[:120])

check.run_on_error = True
