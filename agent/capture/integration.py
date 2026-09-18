"""Explicit local operations for Step 6.5B. No schedule is installed."""
import argparse
import json
from pathlib import Path
from cryptography.hazmat.primitives import serialization
from .service import Store
from .secure_sync import Poller,un64,private_path,encrypted_backup,restore_backup
from .curator import run_once
from .bridge import stage,snapshot,decide
from ..intake import IntakeConfig

def main(argv=None):
    parser=argparse.ArgumentParser(description='Dormant private capture integration')
    parser.add_argument('--config',type=Path,required=True,help='Protected local configuration outside Git')
    parser.add_argument('command',choices=['poll-once','curate-once','review','decide','backup','restore'])
    parser.add_argument('--select',nargs='*')
    parser.add_argument('--snapshot')
    parser.add_argument('--decision',choices=['approve-private','reject'])
    parser.add_argument('--reviewer')
    parser.add_argument('--destination',type=Path)
    parser.add_argument('--source',type=Path)
    args=parser.parse_args(argv)
    c=json.loads(private_path(args.config).read_text())
    if c.get('enabled') is not True:raise ValueError('Integration disabled in local configuration')
    root=private_path(c['privateRoot'])
    cfg=IntakeConfig(Path(c['publicBaseline']),root/'master.json',root/'staging.json',root/'logs')
    backup_key=un64(private_path(c['backupKeyFile']).read_text().strip())
    if args.command=='restore':
        if not args.source or not args.destination:raise ValueError('Choose backup and new restore directory')
        result=restore_backup(args.source,args.destination,backup_key).health()
    else:
        store=Store(root/'capture.sqlite3')
        if args.command=='poll-once':
            if c.get('independentBackupConfirmed') is not True:raise ValueError('Confirm independent backup device first')
            transport_key=un64(private_path(c['syncKeyFile']).read_text().strip())
            envelope_keys={k:un64(private_path(v).read_text().strip()) for k,v in c['envelopeKeyFiles'].items()}
            recipient=serialization.load_pem_private_key(private_path(c['recipientPrivateKeyFile']).read_bytes(),None)
            poller=Poller(store,c['origin'],c['computerKeyId'],transport_key,envelope_keys,recipient,c['recipientKeyId'],c['backupDirectory'],backup_key)
            result=poller.run_once()
        elif args.command=='curate-once':
            result=run_once(store);result['staged']=stage(store,cfg)
        elif args.command=='review':result=snapshot(cfg)
        elif args.command=='decide':
            if not all([args.snapshot,args.select,args.decision,args.reviewer]):raise ValueError('Snapshot, explicit selection, decision and reviewer required')
            confirmation=input('Type '+args.decision+' '+args.snapshot+' to confirm the displayed selection: ')
            if confirmation!=args.decision+' '+args.snapshot:raise ValueError('No confirmation')
            result=decide(cfg,args.snapshot,args.select,args.decision,args.reviewer).as_dict()
        elif args.command=='backup':
            if not args.destination:raise ValueError('Choose a new independent backup destination')
            result={'backupHash':encrypted_backup(store,args.destination,backup_key)}
    print(json.dumps(result,indent=2))
    return 0

if __name__=='__main__':raise SystemExit(main())

