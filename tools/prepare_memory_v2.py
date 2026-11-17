"""Validate/convert a candidate file offline; never reads paths embedded in supplied JSON."""
import argparse
import json
from pathlib import Path
from yiban.memory.v2_schema import SCHEMA, validate
from yiban.memory.v2_convert import convert
from yiban.memory.shared_memory import SharedError


def main():
    parser=argparse.ArgumentParser(description='校验新版记忆候选，或将cards候选转换为待审阅的新格式；不连接数据库。')
    parser.add_argument('input',type=Path)
    parser.add_argument('--original',type=Path,help='可选的原始文章，用于核对摘录')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    try:
        if args.input.stat().st_size>4_000_000:raise ValueError('候选文件超过4MB。')
        document=json.loads(args.input.read_text(encoding='utf-8'))
        if isinstance(document,dict) and document.get('schema')==SCHEMA:
            result={'document':validate(document),'warnings':[]}
        else:
            if args.original and args.original.stat().st_size>1_000_000:raise ValueError('原文过大，请分包。')
            result=convert(document,args.original.read_text(encoding='utf-8') if args.original else None)
        if args.output.exists():raise ValueError('输出文件已存在，请使用新的文件名，避免覆盖审阅结果。')
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(result['document'],ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print('已生成待审阅候选：'+str(args.output.resolve()))
        print('条目：'+str(len(result['document']['records']))+'；没有写入数据库。')
        for warning in result['warnings']:print('待核对：'+warning)
    except (OSError,ValueError,SharedError) as error:
        parser.exit(1,str(error)+'\n')


if __name__=='__main__':main()
