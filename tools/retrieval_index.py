"""Inspect or reset the derived vector cache. Never touches memories or voiceprints."""
import argparse
from datetime import datetime
from pathlib import Path

from yiban.settings import ROOT, load_settings
from yiban.memory.retrieval import VectorCache


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,default=ROOT/'config/mac.json')
    parser.add_argument('--reset',action='store_true',help='Stop the assistant first. Archive the cache; subsequent queries rebuild it.')
    args=parser.parse_args();config=load_settings(args.config)
    db=Path(config['memory']['database'])
    if not db.is_absolute():db=ROOT/db
    path=db.parent/'retrieval/vectors.sqlite3'
    if not path.exists():print('尚无向量索引，下次查询时自动建立。');return
    if args.reset:
        backup=path.with_name('vectors-reset-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f')+'.sqlite3')
        path.rename(backup)
        print('已归档派生索引；重启后查询会按当前授权记录重建。记忆和声纹未改变。');return
    try:
        with VectorCache(path).connect() as connection:count=connection.execute('SELECT COUNT(*) FROM vectors').fetchone()[0]
        print(f'派生向量条数：{count}；文件大小：{path.stat().st_size} 字节。')
    except Exception:
        print('索引无法读取。请先停止助手，再运行此命令加 --reset；不要删除原始记忆数据库。')
        raise SystemExit(1)


if __name__=='__main__':main()
