from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256


class AttachmentUseCases:
    def upload_attachment(
        self,
        token: str,
        *,
        filename: str,
        mime_type: str,
        content: bytes,
        idempotency_key: str,
    ):
        actor = self.actor_from_token(token, "attachment:write")
        request_hash = sha256(
            content + filename.encode() + mime_type.encode()
        ).hexdigest()

        def run():
            attachment = self.attachments.ingest(
                filename=filename,
                mime_type=mime_type,
                content=content,
            )
            draft = self.drafts.create(
                memo=f"Review attachment {attachment.original_filename}",
                proposed_postings=[],
                missing_fields=["amount", "source_account_id", "expense_account_id"],
                source="ocr",
                confidence=0.0,
                attachment_id=attachment.attachment_id,
            )
            self.audit.record(
                operation="attachment.upload",
                actor=actor,
                entity_ref=attachment.attachment_id,
                details={"attachment": asdict(attachment), "draft_id": draft.draft_id},
            )
            return {"attachment": attachment, "draft": draft}

        result, replay = self.idempotency.run(
            key=idempotency_key,
            actor=actor,
            operation="attachment.upload",
            request_hash=request_hash,
            fn=run,
        )
        self._commit_replay_or(
            replay,
            lambda: self._commit_attachment_change(
                    attachments=(result["attachment"],),
                    attachment_contents={result["attachment"].attachment_id: content},
                    drafts=(result["draft"],),
            ),
        )
        return result, replay
