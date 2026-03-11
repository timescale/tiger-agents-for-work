from pydantic import BaseModel


class PromptPackage(BaseModel):
    package_name: str
    package_path: str = "templates"
