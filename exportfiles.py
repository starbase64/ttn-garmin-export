"""Publish one current export without discarding unrelated files or failed-write backups."""
import json
import os
from pathlib import Path,PurePosixPath


def safe_target(root,name):
    if not isinstance(name,str) or not name or '\\' in name or ':' in name:
        raise ValueError('Invalid export path')
    if PurePosixPath(name).as_posix()!=name:raise ValueError('Non-canonical export path')
    parts=PurePosixPath(name).parts
    if not parts or PurePosixPath(name).is_absolute() or '..' in parts:
        raise ValueError('Invalid export path')
    target=root
    if root.is_symlink():raise ValueError('Export directory is a symlink')
    for part in parts:
        target=target/part
        if target.is_symlink():raise ValueError('Export path is a symlink')
    return target


def publish_export(stage,destination):
    """Replace generated files, remove stale generated files, preserve manual files.

    Staging completes before publication. Files being replaced are moved into
    the staging directory first so ordinary publication errors can roll back.
    The report is installed last and records exactly which files we own.
    """
    stage=Path(stage);destination=Path(destination)
    safe_target(destination,'report.json')
    previous=set()
    try:
        report=json.loads((destination/'report.json').read_text(encoding='utf-8'))
        if report.get('app')=='ttn-garmin-export' and isinstance(report.get('generated_files'),list):
            previous=set(report['generated_files'])
    except (OSError,ValueError,TypeError,AttributeError):pass
    incoming={p.relative_to(stage).as_posix() for p in stage.rglob('*') if p.is_file()}
    touched=incoming|previous
    targets={name:safe_target(destination,name) for name in touched}
    destination.mkdir(parents=True,exist_ok=True)
    backup=stage/'.previous';moved=[];installed=[]
    try:
        for name in sorted(touched):
            target=targets[name]
            if target.exists():
                if not target.is_file():raise ValueError('Export file conflicts with a directory')
                old=backup/name;old.parent.mkdir(parents=True,exist_ok=True)
                os.replace(target,old);moved.append(name)
        for name in sorted(incoming-{'report.json'})+(['report.json'] if 'report.json' in incoming else []):
            target=targets[name];target.parent.mkdir(parents=True,exist_ok=True)
            os.replace(stage/name,target);installed.append(name)
    except Exception:
        for name in reversed(installed):targets[name].unlink(missing_ok=True)
        for name in reversed(moved):os.replace(backup/name,targets[name])
        raise
