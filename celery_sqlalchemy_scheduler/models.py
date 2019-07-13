# coding=utf-8

import datetime as dt

import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy.orm import relationship, foreign, remote
from celery import current_app, schedules
from celery.utils.log import get_logger
from dateutil import tz

from .tzcrontab import TzAwareCrontab
from .session import ModelBase
from .literals import MICROSECONDS, SECONDS, MINUTES, HOURS

logger = get_logger('celery_sqlalchemy_scheduler.models')


def cronexp(field):
    """Representation of cron expression."""
    return field and str(field).replace(' ', '') or '*'


class ModelMixin(object):

    @classmethod
    def create(cls, **kw):
        return cls(**kw)

    def update(self, **kw):
        for attr, value in kw.items():
            setattr(self, attr, value)
        return self


class IntervalSchedule(ModelBase, ModelMixin):
    __tablename__ = 'celery_interval_schedule'
    __table_args__ = {'sqlite_autoincrement': True}

    id = sa.Column(sa.Integer, primary_key=True, autoincrement=True)

    every = sa.Column(sa.Integer, nullable=False)
    period = sa.Column(sa.String(24))

    def __repr__(self):
        if self.every == 1:
            return 'every {0.period_singular}'.format(self)
        return 'every {0.every} {0.period}'.format(self)

    @property
    def schedule(self):
        return schedules.schedule(
            dt.timedelta(**{self.period: self.every}),
            # nowfun=lambda: make_aware(now())
            # nowfun=dt.datetime.now
        )

    @classmethod
    def from_schedule(cls, session, schedule, period=SECONDS):
        every = max(schedule.run_every.total_seconds(), 0)
        model = session.query(IntervalSchedule).filter_by(
            every=every, period=period).first()
        if not model:
            model = cls(every=every, period=period)
            session.add(model)
            session.commit()
        return model

    def period_singular(self):
        return self.period[:-1]

    @classmethod
    def __declare_last__(cls):
        @event.listens_for(cls, 'after_insert')
        def receive_after_insert_for_cache_query(mapper, conn, target):
            """注册Mapper事件，监听insert之后

            :param target: 模型
            """
            logger.debug('after_insert {}'.format(target))

        @event.listens_for(cls, 'after_update')
        def receive_after_update_for_cache_query(mapper, conn, target):
            """注册Mapper事件，监听update之后

            :param target: 模型
            """
            logger.debug('after_update {}'.format(target))

        @event.listens_for(cls, 'after_delete')
        def receive_after_delete_for_cache_query(mapper, conn, target):
            """注册Mapper事件，监听delete之后

            :param target: 模型
            """
            logger.debug('after_delete {}'.format(target))


class CrontabSchedule(ModelBase, ModelMixin):
    __tablename__ = 'celery_crontab_schedule'
    __table_args__ = {'sqlite_autoincrement': True}

    id = sa.Column(sa.Integer, primary_key=True, autoincrement=True)
    minute = sa.Column(sa.String(60 * 4), default='*')
    hour = sa.Column(sa.String(24 * 4), default='*')
    day_of_week = sa.Column(sa.String(64), default='*')
    day_of_month = sa.Column(sa.String(31 * 4), default='*')
    month_of_year = sa.Column(sa.String(64), default='*')
    timezone = sa.Column(sa.String(64), default='UTC')

    def __repr__(self):
        return '{0} {1} {2} {3} {4} (m/h/d/dM/MY) {5}'.format(
            cronexp(self.minute), cronexp(self.hour),
            cronexp(self.day_of_week), cronexp(self.day_of_month),
            cronexp(self.month_of_year), str(self.timezone)
        )

    @property
    def schedule(self):
        return TzAwareCrontab(
            minute=self.minute,
            hour=self.hour, day_of_week=self.day_of_week,
            day_of_month=self.day_of_month,
            month_of_year=self.month_of_year,
            # tz=tz.gettz(self.timezone),
            # app=current_app
        )

    @classmethod
    def from_schedule(cls, session, schedule):
        spec = {
            'minute': schedule._orig_minute,
            'hour': schedule._orig_hour,
            'day_of_week': schedule._orig_day_of_week,
            'day_of_month': schedule._orig_day_of_month,
            'month_of_year': schedule._orig_month_of_year,
        }
        if schedule.tz:
            spec.update({
                'timezone': schedule.tz.zone
            })
        model = session.query(CrontabSchedule).filter_by(**spec).first()
        if not model:
            model = cls(**spec)
            session.add(model)
            session.commit()
        return model


class SolarSchedule(ModelBase, ModelMixin):
    __tablename__ = 'celery_solar_schedule'
    __table_args__ = {'sqlite_autoincrement': True}

    id = sa.Column(sa.Integer, primary_key=True, autoincrement=True)

    event = sa.Column(sa.String(24))
    latitude = sa.Column(sa.Float())
    longitude = sa.Column(sa.Float())

    @property
    def schedule(self):
        return schedules.solar(
            self.event,
            self.latitude,
            self.longitude,
            nowfun=dt.datetime.now
        )

    @classmethod
    def from_schedule(cls, session, schedule):
        spec = {
            'event': schedule.event,
            'latitude': schedule.lat,
            'longitude': schedule.lon
        }
        model = session.query(SolarSchedule).filter_by(**spec).first()
        if not model:
            model = cls(**spec)
            session.add(model)
            session.commit()
        return model

    def __repr__(self):
        return '{0} ({1}, {2})'.format(
            self.event,
            self.latitude,
            self.longitude
        )


class PeriodicTaskChanged(ModelBase, ModelMixin):
    """Helper table for tracking updates to periodic tasks."""

    __tablename__ = 'celery_periodic_task_changed'

    id = sa.Column(sa.Integer, primary_key=True)
    last_update = sa.Column(
        sa.DateTime(timezone=True), nullable=False, default=dt.datetime.now)

    @classmethod
    def changed(cls, instance, session):
        """
        :param instance: PeriodicTask
        :param session:
        """
        if not instance.no_changes:
            cls.update_changed()

    @classmethod
    def update_changed(cls, session):
        periodic_tasks = session.query(PeriodicTaskChanged).get(1)
        if not periodic_tasks:
            periodic_tasks = PeriodicTaskChanged(id=1)
        periodic_tasks.last_update = dt.datetime.now()
        session.add(periodic_tasks)
        session.commit()

    @classmethod
    def last_change(cls, session):
        periodic_tasks = session.query(PeriodicTaskChanged).get(1)
        if periodic_tasks:
            return periodic_tasks.last_update


class PeriodicTask(ModelBase, ModelMixin):

    __tablename__ = 'celery_periodic_task'
    __table_args__ = {'sqlite_autoincrement': True}

    id = sa.Column(sa.Integer, primary_key=True, autoincrement=True)
    # name
    name = sa.Column(sa.String(255), unique=True)
    # task name
    task = sa.Column(sa.String(255))

    interval_id = sa.Column(sa.Integer)
    interval = relationship(
        IntervalSchedule,
        uselist=False,
        primaryjoin=foreign(interval_id) == remote(IntervalSchedule.id)
    )

    crontab_id = sa.Column(sa.Integer)
    crontab = relationship(
        CrontabSchedule,
        uselist=False,
        primaryjoin=foreign(crontab_id) == remote(CrontabSchedule.id)
    )

    solar_id = sa.Column(sa.Integer)
    solar = relationship(
        SolarSchedule,
        uselist=False,
        primaryjoin=foreign(solar_id) == remote(SolarSchedule.id)
    )

    # 参数
    args = sa.Column(sa.Text(), default='[]')
    kwargs = sa.Column(sa.Text(), default='{}')
    # 队列
    queue = sa.Column(sa.String(255))
    # 交换器
    exchange = sa.Column(sa.String(255))
    # 路由键
    routing_key = sa.Column(sa.String(255))
    # 优先级
    priority = sa.Column(sa.Integer())

    expires = sa.Column(sa.DateTime(timezone=True))

    one_off = sa.Column(sa.Boolean(), default=False)

    # 开始时间
    start_time = sa.Column(sa.DateTime(timezone=True))
    # 使能/禁能
    enabled = sa.Column(sa.Boolean(), default=True)
    # 最后运行时间
    last_run_at = sa.Column(sa.DateTime(timezone=True))
    # 总运行次数
    total_run_count = sa.Column(sa.Integer(), nullable=False, default=0)
    # 修改时间
    date_changed = sa.Column(sa.DateTime(timezone=True),
                             default=dt.datetime.now, onupdate=dt.datetime.now)
    # 说明
    description = sa.Column(sa.Text(), default='')

    no_changes = False

    def __repr__(self):
        fmt = '{0.name}: {{no schedule}}'
        if self.interval:
            fmt = '{0.name}: {0.interval}'
        elif self.crontab:
            fmt = '{0.name}: {0.crontab}'
        elif self.solar:
            fmt = '{0.name}: {0.solar}'
        return fmt.format(self)

    @property
    def task_name(self):
        return self.task

    @task_name.setter
    def task_name(self, value):
        self.task = value

    @property
    def schedule(self):
        if self.interval:
            return self.interval.schedule
        elif self.crontab:
            return self.crontab.schedule
        elif self.solar:
            return self.solar.schedule
        raise ValueError('{} schedule is None!'.format(self.name))

    @classmethod
    def __declare_last__(cls):
        @event.listens_for(cls, 'after_insert')
        def receive_after_insert_for_cache_query(mapper, conn, target):
            """注册Mapper事件，监听insert之后

            :param target: 模型
            """
            logger.debug('after_insert {}'.format(target))

        @event.listens_for(cls, 'after_update')
        def receive_after_update_for_cache_query(mapper, conn, target):
            """注册Mapper事件，监听update之后

            :param target: 模型
            """
            logger.debug('after_update {}'.format(target))

        @event.listens_for(cls, 'after_delete')
        def receive_after_delete_for_cache_query(mapper, conn, target):
            """注册Mapper事件，监听delete之后

            :param target: 模型
            """
            logger.debug('after_delete {}'.format(target))
