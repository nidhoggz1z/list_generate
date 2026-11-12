"""Reusable business input and mutually exclusive task response contracts."""
import re
from datetime import datetime
from typing import Annotated, Literal, Union

from pydantic import (
    BaseModel, ConfigDict, Field, StringConstraints, field_validator,
    model_serializer, model_validator,
)
from .material_tree import iter_materials

NonBlank = Annotated[str, StringConstraints(strict=True, min_length=1, pattern=r'\S')]
Identifier = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)]
Amount = Annotated[str, StringConstraints(strict=True, pattern=r'^$|^(0|[1-9][0-9]*)(\.[0-9]+)?$')]
TaskStatus = Literal['PENDING', 'PROCESSING', 'DONE', 'FAILED', 'TERMINATED']


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class TaskIdentity(Contract):
    bsnEvtId: Identifier
    userNo: Identifier
    taskId: Identifier


class BusinessObject(Contract):
    """Only known optional null fields are ignored; extensions remain opaque data."""
    model_config = ConfigDict(extra='allow', strict=True)

    @model_validator(mode='before')
    @classmethod
    def ignore_optional_null(cls, value):
        if isinstance(value, dict):
            return {k: v for k, v in value.items() if v is not None or
                    k not in cls.model_fields or cls.model_fields[k].is_required()}
        return value

    @model_serializer(mode='wrap')
    def omit_absent_fields(self, handler):
        result = handler(self)
        for name in type(self).model_fields:
            if result.get(name) is None:
                result.pop(name, None)
        return result


class Collateral(BusinessObject):
    cltlId: str | None = None
    cltlTp: str | None = None
    cltlNm: str | None = None


class JointGuarantor(BusinessObject):
    cstId: str | None = None
    cstNm: str | None = None
    idCardNo: str | None = None


class Subcontract(BusinessObject):
    subcontractNum: str | None = None
    gnrNm: str | None = None
    gnrId: str | None = None
    frmCtrTpcd: str | None = None
    frmCtrTpNm: str | None = None
    jntGnrInfs: list[JointGuarantor] | None = None
    SubcontractCollateralRels: list[Collateral] | None = None


class BusinessData(BusinessObject, TaskIdentity):
    model_config = ConfigDict(extra='allow', strict=True)

    contractNum: str | None = None
    replyId: str | None = None
    cstId: str | None = None
    cstNm: str | None = None
    ctrCharClcd: str | None = None
    ctrCharClNm: str | None = None
    contractNatureCode: str | None = None
    contractNatureNm: str | None = None
    aspdId: str | None = None
    aspdNm: str | None = None
    grtstlRcmbCd: str | None = None
    grtstlRcmbNm: str | None = None
    primGrtstl: str | None = None
    primGrtstlNm: str | None = None
    lwrsInd: str | None = None
    lwrsIndNm: str | None = None
    cprsvPdPoolInd: str | None = None
    cprsvPdPoolIndNm: str | None = None
    replyImageUrl: str | None = None
    expdtrHpnChar: str | None = None
    expdtrHpnNm: str | None = None
    expdtrCharCd: str | None = None
    expdtrCharNm: str | None = None
    ccy: str | None = None
    ctrTamt: Amount | None = None
    crlnFndsPymdCd: str | None = None
    crlnFndsPymdNm: str | None = None
    payOutAspdId: str | None = None
    payOutAspdNm: str | None = None
    subcontracts: list[Subcontract] | None = None


class CreateTaskRequest(BusinessData):
    prcsStage: NonBlank = Field(description='中文流程名称，用于一级目录过滤')
    aspdNm: Identifier = Field(description='本次生成材料的唯一目标产品中文名')
    replyImageUrl: NonBlank = Field(description='批复 PDF 的完整 HTTP(S) URL 或未编码影像相对路径，最大 10 MiB；完整 URL 原样使用')


class CreateResponse(TaskIdentity):
    autoReply: Literal['task created']


class FileContentRequest(Contract):
    fileName: NonBlank


class TaskStatusResponse(TaskIdentity):
    status: Literal['PENDING', 'PROCESSING', 'TERMINATED']


class TaskFailureResponse(TaskIdentity):
    status: Literal['FAILED']
    reason: NonBlank


class ReplyResponse(Contract):
    reply: NonBlank


class TaskNotCreatedResponse(Contract):
    reply: Literal['任务未创建']


class ProductMetadata(Contract):
    extractedProductName: NonBlank
    productName: NonBlank
    checklistFileName: NonBlank


class MaterialItem(Contract):
    itemId: Annotated[str, StringConstraints(pattern=r'^R[0-9]{4,}$')]
    itemType: Literal['MATERIAL']
    productName: NonBlank
    materialName: str
    sourceSystem: Annotated[str, StringConstraints(min_length=1)]
    policyBasis: str
    publishDate: Annotated[str, StringConstraints(min_length=1)]
    publishDepartment: Annotated[str, StringConstraints(min_length=1)]
    applicability: Literal['APPLICABLE']
    requirement: str


class HistoricalMaterialItem(MaterialItem):
    """Keep saved material fields intact when reading historical results."""
    sourceRuleId: NonBlank
    reason: str


class ResultMetadata(Contract):
    generatedAt: str
    total: Annotated[int, Field(ge=1)]
    products: Annotated[list[ProductMetadata], Field(min_length=1)]

    @field_validator('generatedAt')
    @classmethod
    def require_generation_time(cls, value):
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(\.\d+)?([Zz]|[+-]\d{2}:\d{2})', value):
            raise ValueError('generatedAt must be RFC 3339 with timezone')
        datetime.fromisoformat(value.upper())
        return value


class LegacyTaskResult(ResultMetadata):
    """Internal validation of persisted flat results; never an API response."""
    sourceDocumentHash: Annotated[str, StringConstraints(pattern=r'^[0-9a-f]{64}$')]
    items: Annotated[list[HistoricalMaterialItem], Field(min_length=1)]

    @model_validator(mode='after')
    def consistent_items(self):
        if self.total != len(self.items):
            raise ValueError('total must equal items length')
        names = {p.productName for p in self.products}
        for index, item in enumerate(self.items, 1):
            if item.itemId != f'R{index:04d}':
                raise ValueError('item IDs must be sequential from R0001')
            if item.productName not in names:
                raise ValueError('item productName must occur in products')
            if item.materialName and not item.materialName.startswith(f'[{item.productName}]'):
                raise ValueError('materialName must have its product prefix')
        return self


class MaterialCategory(Contract):
    categoryId: NonBlank
    categoryName: NonBlank
    children: list['MaterialCategory']
    materials: list[Union[MaterialItem, HistoricalMaterialItem]]

    @model_validator(mode='after')
    def nonempty_directory(self):
        if not self.children and not self.materials:
            raise ValueError('empty directories must be pruned')
        if len({c.categoryId for c in self.children}) != len(self.children):
            raise ValueError('sibling category IDs must be unique')
        return self


class ProductMaterials(Contract):
    productName: NonBlank
    categories: list[MaterialCategory]

    @model_validator(mode='after')
    def unique_roots(self):
        if len({c.categoryId for c in self.categories}) != len(self.categories):
            raise ValueError('root category IDs must be unique within a product')
        return self


class TaskResult(ResultMetadata):
    items: Annotated[list[ProductMaterials], Field(min_length=1)]

    @model_validator(mode='after')
    def consistent_items(self):
        names = [p.productName for p in self.products]
        if len(set(names)) != len(names) or [p.productName for p in self.items] != names:
            raise ValueError('item product trees must follow the exact products list')
        index = 0
        for product in self.items:
            for item in iter_materials([product.model_dump()]):
                index += 1
                if item['itemId'] != f'R{index:04d}':
                    raise ValueError('item IDs must be sequential in tree display order')
                if item['productName'] != product.productName:
                    raise ValueError('material must belong to its product tree')
                if item['materialName'] and not item['materialName'].startswith(f'[{product.productName}]'):
                    raise ValueError('materialName must have its product prefix')
        if self.total != index:
            raise ValueError('total must equal material leaf count')
        return self


class HistoricalTaskResult(TaskResult):
    """Preserve the original public hash on results saved before its removal."""
    sourceDocumentHash: Annotated[str, StringConstraints(pattern=r'^[0-9a-f]{64}$')]


class TaskDoneResponse(TaskIdentity):
    status: Literal['DONE']
    message: Literal['生成完成']
    data: Union[TaskResult, HistoricalTaskResult]


TaskStateResponse = Annotated[
    Union[TaskStatusResponse, TaskFailureResponse, TaskDoneResponse], Field(discriminator='status')
]
TaskQueryResponse = Union[TaskStateResponse, TaskNotCreatedResponse]


class CallbackResult(TaskResult):
    generatedAt: Annotated[str, StringConstraints(pattern=r'^\d{8} \d{8}$')]

    @field_validator('generatedAt')
    @classmethod
    def require_generation_time(cls, value):
        datetime.strptime(value, '%Y%m%d %H%M%S%f')
        return value


class HistoricalCallbackResult(CallbackResult):
    sourceDocumentHash: Annotated[str, StringConstraints(pattern=r'^[0-9a-f]{64}$')]


class TaskDoneCallback(TaskIdentity):
    status: Literal['DONE']
    message: Literal['生成完成']
    data: Union[CallbackResult, HistoricalCallbackResult]


class TaskTerminatedCallback(TaskIdentity):
    status: Literal['TERMINATED']


TaskCallbackResponse = Annotated[
    Union[TaskDoneCallback, TaskFailureResponse, TaskTerminatedCallback], Field(discriminator='status')
]
