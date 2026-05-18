from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class ParameterProperty(BaseModel):
    """One nested property of a method parameter."""

    name: str = Field(..., description="Property name, for example: role, id, email")
    class_name: str = Field(
        ...,
        description="Property class/type, for example: str, int, Role, unknown",
    )
    description: str = Field(..., description="Short textual description of the property")


class MethodParameter(BaseModel):
    """Method/function parameter description."""

    name: str = Field(..., description="Parameter name exactly as it appears in the signature")
    class_name: str = Field(
        ...,
        description="Parameter class/type. Use 'unknown' if it cannot be determined from the code.",
    )
    description: str = Field(..., description="Short textual description of the parameter")
    properties: list[ParameterProperty] = Field(
        default_factory=list,
        description="Nested parameter properties. Do not use more than one nesting level.",
    )


class RelatedSymbol(BaseModel):
    """Related function, method or class found during analysis."""

    name: str = Field(..., description="Related symbol name")
    relation: str = Field(
        ...,
        description="Relation to the main method, for example: calls, called_by, uses_class",
    )
    description: str = Field(..., description="Short description of the relation")


class MethodDocumentation(BaseModel):
    """Validated JSON structure for generated code documentation."""

    exact_method_name: str = Field(
        ...,
        description="Exact short name of the described method/function",
    )
    qualified_name: Optional[str] = Field(
        default=None,
        description="Full qualified name if known",
    )
    file_path: Optional[str] = Field(
        default=None,
        description="Relative file path if known",
    )
    description: str = Field(..., description="Textual description of the method/function logic")
    parameters: list[MethodParameter] = Field(default_factory=list)
    related_symbols: list[RelatedSymbol] = Field(default_factory=list)
