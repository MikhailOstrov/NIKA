from sc_kpm import ScModule
from .HuntingKnowledgeAgent import HuntingKnowledgeAgent


class MessageProcessingModule(ScModule):
    def __init__(self, agent=None):
        super().__init__(agent if agent is not None else HuntingKnowledgeAgent())
