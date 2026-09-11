"""Local operator commands. No remote controls and no scheduler installation."""
import argparse
import json
from pathlib import Path
from .service import Store,make_server
from .curator import run_once,review

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--store',type=Path,required=True,help='SQLite path outside any Git checkout')
    sub=p.add_subparsers(dest='command',required=True)
    for command in ['status','pause','resume','curate']:sub.add_parser(command)
    r=sub.add_parser('review');r.add_argument('--expanded',action='store_true');r.add_argument('--group-by',choices=['topic','project','person','entity'],default='topic')
    r=sub.add_parser('retry');r.add_argument('receipt')
    r=sub.add_parser('backup');r.add_argument('destination',type=Path)
    r=sub.add_parser('serve');r.add_argument('--key-file',type=Path,required=True);r.add_argument('--port',type=int,default=8786)
    args=p.parse_args();store=Store(args.store)
    if args.command=='serve':
        key=args.key_file.resolve()
        if any((x/'.git').exists() for x in key.parents):p.error('Key file must be outside Git')
        secret=key.read_bytes()
        if len(secret)<32:p.error('Key must contain at least 32 random bytes')
        server=make_server(store,{'local':secret},port=args.port)
        try:server.serve_forever()
        finally:server.server_close()
        return
    if args.command in {'pause','resume'}:store.set_enabled(args.command=='resume')
    if args.command=='retry':store.retry(args.receipt)
    if args.command=='backup':store.backup(args.destination)
    result=review(store,args.group_by,args.expanded) if args.command=='review' else run_once(store) if args.command=='curate' else store.health()
    print(json.dumps(result,indent=2))

if __name__=='__main__':main()
