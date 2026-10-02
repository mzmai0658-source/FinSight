"""作品说明：明确记录保存失败，避免已提交的同批其他部分被误认为整体成功。"""


class RecordNotImportable(ValueError):
    pass


class BatchSaveError(RuntimeError):
    def __init__(self, report: dict):
        self.report = report
        super().__init__(f"Database batch: {report['committed']} committed, {report['failed']} failed, {report['skipped']} skipped")
