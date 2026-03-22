import re

from grab.models.schemas import DoctorPageTarget, GrabConfig, MemberProfile


class SessionCaptureService:
    def __init__(
        self,
        page,
        config: GrabConfig,
        prompt_enter=None,
        prompt_text=None,
    ):
        self.page = page
        self.config = config
        self.prompt_enter = prompt_enter or (lambda message: input(message))
        self.prompt_text = prompt_text or (lambda message: input(message))

    def parse_doctor_page_url(self, url: str) -> DoctorPageTarget:
        match = re.fullmatch(
            r"https://www\.91160\.com/doctors/index/unit_id-(?P<unit_id>[^/]+)/dep_id-(?P<dept_id>[^/]+)/docid-(?P<doctor_id>[^/.]+)\.html",
            url,
        )
        if match is None:
            raise ValueError("Only doctor detail pages are supported for now")

        return DoctorPageTarget(
            unit_id=match.group("unit_id"),
            dept_id=match.group("dept_id"),
            doctor_id=match.group("doctor_id"),
            source_url=url,
        )

    async def capture_target_from_current_page(self) -> DoctorPageTarget:
        self.prompt_enter("请先完成登录并停留在目标医生页，准备好后按 Enter 继续: ")
        return self.parse_doctor_page_url(self.page.url)

    async def fetch_member_profiles(self) -> list[MemberProfile]:
        await self.page.goto("https://user.91160.com/member.html")
        return self.parse_member_profiles(await self.page.content())

    def parse_member_profiles(self, html: str) -> list[MemberProfile]:
        rows = re.findall(
            r'<tr id="mem(?P<member_id>[^"]+)">.*?<td>(?P<name>[^<]+)</td>.*?<td>(?P<status>[^<]+)</td>.*?</tr>',
            html,
            re.S,
        )
        if not rows:
            raise ValueError("No members found in member.html")

        return [
            MemberProfile(
                member_id=member_id,
                name=name,
                certified=status == "已认证",
            )
            for member_id, name, status in rows
        ]

    async def resolve_member_id(self) -> str:
        members = await self.fetch_member_profiles()
        member_map = {member.member_id: member for member in members}

        if self.config.member_id is not None:
            if self.config.member_id not in member_map:
                raise ValueError("Configured member_id was not found in current account")
            return self.config.member_id

        member_lines = [
            f"{member.member_id}: {member.name}"
            + ("" if member.certified else "（未认证）")
            for member in members
        ]
        selected = self.prompt_text(
            "请选择就诊人编号: " + " / ".join(member_lines) + " "
        ).strip()
        if selected not in member_map:
            raise ValueError("Selected member_id was not found in current account")
        return selected
