from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session, joinedload

from app.auth import authenticate_user, create_access_token, get_current_user, require_bioops
from app.database import SessionLocal, get_db
from app.models import Job, JobStage, Sample
from app.pipeline.runner import create_job_stages, run_pipeline_sync
from app.schemas import (
    HealthOut,
    JobCreate,
    JobListItem,
    JobOut,
    LoginRequest,
    SampleOut,
    StageOut,
    TokenResponse,
)


router = APIRouter(prefix="/api")


def _run_job_background(job_id: int) -> None:
    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if job:
            run_pipeline_sync(db, job)
    finally:
        db.close()


@router.get("/health", response_model=HealthOut)
def health():
    return HealthOut(status="ok", service="fastq-qc-pipeline")


@router.post("/auth/login", response_model=TokenResponse)
def login(body: LoginRequest):
    user = authenticate_user(body.username.strip(), body.password)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
    token = create_access_token(user["username"], user["role"])
    return TokenResponse(
        access_token=token,
        username=user["username"],
        role=user["role"],
    )


@router.get("/samples", response_model=list[SampleOut])
def list_samples(_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.query(Sample).order_by(Sample.id).all()


@router.post("/jobs", response_model=JobOut, status_code=status.HTTP_201_CREATED)
def create_job(
    body: JobCreate,
    background: BackgroundTasks,
    user: dict = Depends(require_bioops),
    db: Session = Depends(get_db),
):
    sample_id = body.sampleId
    fastq_text = (body.fastqText or "").strip() if body.fastqText else ""
    sample_name = "自定义输入"
    sample = None

    if sample_id is not None:
        sample = db.query(Sample).filter(Sample.id == sample_id).first()
        if not sample:
            raise HTTPException(status_code=404, detail="样例不存在")
        fastq_text = sample.fastq_content
        sample_name = sample.name
    elif not fastq_text:
        raise HTTPException(status_code=400, detail="请提供 sampleId 或 fastqText")

    job = Job(
        sample_id=sample.id if sample else None,
        sample_name=sample_name,
        status="pending",
        created_by=user["username"],
        fastq_snapshot=fastq_text,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    create_job_stages(db, job.id)
    background.add_task(_run_job_background, job.id)

    job = (
        db.query(Job)
        .options(joinedload(Job.stages))
        .filter(Job.id == job.id)
        .first()
    )
    return job


@router.get("/jobs", response_model=list[JobListItem])
def list_jobs(_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.query(Job).order_by(Job.id.desc()).all()


# 注意：必须注册在 /jobs/{job_id} 之前，否则 "search" 会被当作 job_id 匹配
@router.get("/jobs/search", response_model=list[JobListItem])
def search_jobs(
    min_reads: int | None = Query(default=None, ge=0, description="读段数下限 reads >="),
    min_mean_quality: float | None = Query(
        default=None, ge=0, description="平均质量下限 mean_quality >="
    ),
    max_n_rate: float | None = Query(
        default=None, ge=0, le=1, description="N 率上限 n_rate <="
    ),
    _user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """按指标门槛组合检索历史作业（AND 语义，服务端过滤）。

    无指标的作业（未成功完成）天然不满足任何数值门槛，不会命中；
    未命中时返回空列表，绝不回退为全量。
    """
    if min_reads is None and min_mean_quality is None and max_n_rate is None:
        raise HTTPException(status_code=400, detail="请至少设置一个指标门槛")

    query = db.query(Job).filter(Job.metrics.isnot(None))
    if min_reads is not None:
        query = query.filter(Job.metrics["reads"].as_float() >= min_reads)
    if min_mean_quality is not None:
        query = query.filter(Job.metrics["mean_quality"].as_float() >= min_mean_quality)
    if max_n_rate is not None:
        query = query.filter(Job.metrics["n_rate"].as_float() <= max_n_rate)
    return query.order_by(Job.id.desc()).all()


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: int, _user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    job = (
        db.query(Job)
        .options(joinedload(Job.stages))
        .filter(Job.id == job_id)
        .first()
    )
    if not job:
        raise HTTPException(status_code=404, detail="作业不存在")
    return job


@router.get("/jobs/{job_id}/stages", response_model=list[StageOut])
def get_job_stages(
    job_id: int, _user: dict = Depends(get_current_user), db: Session = Depends(get_db)
):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="作业不存在")
    return (
        db.query(JobStage)
        .filter(JobStage.job_id == job_id)
        .order_by(JobStage.stage_order)
        .all()
    )
