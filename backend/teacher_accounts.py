"""Local trusted operator confirms teacher identity; never called by registration."""
import argparse
from sqlalchemy import create_engine,select
from sqlalchemy.orm import Session

from .app.config import Settings
from .app.models import User
from .app.schemas import normalize_username


def main():
    parser=argparse.ArgumentParser(description='本机管理员核实真实教师身份后使用；普通用户不能自行确认。')
    parser.add_argument('action',choices=['pending','approve','revoke'])
    parser.add_argument('username',nargs='?')
    args=parser.parse_args()
    engine=create_engine(Settings.load().database_url,hide_parameters=True)
    try:
        with Session(engine) as db,db.begin():
            if args.action=='pending':
                rows=db.scalars(select(User).where(User.academic_identity=='teacher',User.teacher_verified.is_(False),User.is_active.is_(True)))
                for row in rows:
                    print(row.username)
                return
            if not args.username:
                parser.error('approve/revoke需要精确用户名')
            username=normalize_username(args.username)
            user=db.scalar(select(User).where(User.username==username).with_for_update())
            if not user or not user.is_active:
                raise SystemExit('账号不存在或已停用')
            if args.action=='approve':
                user.academic_identity='teacher';user.teacher_verified=True
            else:
                user.teacher_verified=False
            print('Teacher identity updated. Existing team/project roles are unchanged.')
    finally:
        engine.dispose()


if __name__=='__main__':
    main()
