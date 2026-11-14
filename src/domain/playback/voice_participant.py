"""Channel-scoped speaker supplied by the local voice receiver."""


class VoiceParticipant(str):
    area: str
    channel: str

    def __new__(cls, rtc_uid: str, area: str, channel: str):
        instance = super().__new__(cls, 'voice:' + rtc_uid)
        instance.area = area
        instance.channel = channel
        return instance
