#include "StandardMessageReplyAgent.hpp"

#include "keynodes/DialogKeynodes.hpp"
#include "keynodes/MessageKeynodes.hpp"
#include <common/utils/ActionUtils.hpp>
#include <sc-agents-common/utils/IteratorUtils.hpp>

using namespace utils;

namespace dialogControlModule
{

StandardMessageReplyAgent::StandardMessageReplyAgent()
{
  m_logger = utils::ScLogger(
      utils::ScLogger::ScLogType::File, "logs/StandardMessageReplyAgent.log", utils::ScLogLevel::Debug, true);
}

ScResult StandardMessageReplyAgent::DoProgram(ScActionInitiatedEvent const & event, ScAction & action)
{
  ScAddr messageNode = action.GetArgument(ScKeynodes::rrel_1);
  if (!messageNode.IsValid())
  {
    m_logger.Debug("The action doesn't have a message node");
  }

  ScAddr logicRuleNode = generateReplyMessage(messageNode);
  if (!m_context.IsElement(logicRuleNode))
  {
    ScAddr existingReply =
        IteratorUtils::getAnyByOutRelation(&m_context, messageNode, MessageKeynodes::nrel_reply);
    if (m_context.IsElement(existingReply))
    {
      action.SetResult(existingReply);
      return action.FinishSuccessfully();
    }

    // External agents (for example, the hunting knowledge agent) can return a
    // ready-to-display text link as the second argument of this action. When
    // there is no static reply rule, expose that result as the message reply
    // instead of replacing it with the generic "no answer" text.
    ScAddr responseNode = action.GetArgument(ScKeynodes::rrel_2);
    ScAddr responseLink;
    if (m_context.IsElement(responseNode))
      responseLink = IteratorUtils::getAnyFromSet(&m_context, responseNode);

    if (m_context.IsElement(responseLink))
    {
      ScAddr const & replyMessage = m_context.GenerateNode(ScType::NodeConst);
      m_context.GenerateConnector(ScType::ConstPermPosArc, MessageKeynodes::concept_message, replyMessage);
      m_context.GenerateConnector(ScType::ConstPermPosArc, ScKeynodes::lang_ru, responseLink);
      ScAddr const & translationNode = m_context.GenerateNode(ScType::NodeConst);
      m_context.GenerateConnector(ScType::ConstPermPosArc, translationNode, responseLink);
      ScAddr const & translationArc =
          m_context.GenerateConnector(ScType::ConstCommonArc, translationNode, replyMessage);
      m_context.GenerateConnector(
          ScType::ConstPermPosArc, DialogKeynodes::nrel_sc_text_translation, translationArc);
      ScAddr const & replyArc = m_context.GenerateConnector(ScType::ConstCommonArc, messageNode, replyMessage);
      m_context.GenerateConnector(ScType::ConstPermPosArc, MessageKeynodes::nrel_reply, replyArc);
      action.SetResult(replyMessage);
      return action.FinishSuccessfully();
    }

    m_logger.Warning(
        "The reply message isn't generated because reply construction wasn't found through direct inference agent. "
        "Trying to generate default reply message");
    std::stringstream setElementsTextStream;
    ScAddrVector messageClasses;
    setElementsTextStream
        << "Извините, я не нашла ответа на Ваш вопрос. Я определила, что данное сообщение является элементом классов:";
    ScIterator3Ptr it3 = m_context.CreateIterator3(ScType::NodeConst, ScType::EdgeAccessConstPosPerm, messageNode);
    while (it3->Next())
    {
      messageClasses.push_back(it3->Get(0));
    }
    ScAddr const & defaultReplyMessage = m_context.GenerateNode(ScType::NodeConst);
    ScAddr const & defaultReplyLink = m_context.GenerateLink(ScType::LinkConst);
    std::string replyText = setElementsTextStream.str();
    if (!replyText.empty())
      replyText.back() = '.';
    m_context.SetLinkContent(defaultReplyLink, replyText);
    ScTemplate templ;
    templ.Triple(ScKeynodes::lang_ru, ScType::VarPermPosArc, defaultReplyLink);
    templ.Triple(ScType::VarNode >> "_link", ScType::VarPermPosArc, defaultReplyLink);
    templ.Quintuple(
        "_link",
        ScType::VarCommonArc,
        defaultReplyMessage,
        ScType::VarPermPosArc,
        DialogKeynodes::nrel_sc_text_translation);
    templ.Quintuple(
        messageNode, ScType::VarCommonArc, defaultReplyMessage, ScType::VarPermPosArc, MessageKeynodes::nrel_reply);
    ScTemplateResultItem result;
    m_context.GenerateByTemplate(templ, result);
    action.SetResult(defaultReplyMessage);
    return action.FinishSuccessfully();
  }
  ScAddr replyMessageNode = IteratorUtils::getAnyByOutRelation(&m_context, messageNode, MessageKeynodes::nrel_reply);
  m_context.GenerateConnector(ScType::ConstPermPosArc, MessageKeynodes::concept_message, replyMessageNode);
  if (!replyMessageNode.IsValid())
  {
    m_logger.Error("The reply message isn't generated");
    return action.FinishUnsuccessfully();
  }

  m_logger.Debug("The reply message is generated");

  initFields();
  ScAddr langNode = langSearcher->getMessageLanguage(messageNode);

  ScAddr parametersNode = generatePhraseAgentParametersNode(messageNode);

  if (!messageHandler->processReplyMessage(replyMessageNode, logicRuleNode, langNode, parametersNode))
  {
    m_logger.Error("The reply message is formed incorrectly");
    ScIterator5Ptr it5 = IteratorUtils::getIterator5(&m_context, replyMessageNode, MessageKeynodes::nrel_reply, false);
    if (it5->Next())
    {
      m_context.EraseElement(it5->Get(1));
    }

    return action.FinishUnsuccessfully();
  }
  ScAddr responseNode = action.GetArgument(ScKeynodes::rrel_2);

  if (m_context.IsElement(responseNode))
    m_context.GenerateConnector(ScType::ConstTempPosArc, responseNode, replyMessageNode);

  action.SetResult(replyMessageNode);
  return action.FinishSuccessfully();
}

ScAddr StandardMessageReplyAgent::GetActionClass() const
{
  return MessageKeynodes::action_standard_message_reply;
}

ScAddr StandardMessageReplyAgent::generateReplyMessage(const ScAddr & messageNode)
{
  ScAddr logicRuleNode;
  ScAddrVector argsVector = {
      MessageKeynodes::template_reply_target,
      MessageKeynodes::concept_answer_on_standard_message_rule_class_by_priority,
      wrapInSet(messageNode)};

  ScAction actionDirectInference =
      ActionUtils::CreateAction(&m_context, MessageKeynodes::action_direct_inference, argsVector);
  bool const result = actionDirectInference.InitiateAndWait(DIRECT_INFERENCE_AGENT_WAIT_TIME);
  if (result)
  {
    if (actionDirectInference.IsFinishedSuccessfully())
    {
      ScAddr answer = IteratorUtils::getAnyByOutRelation(&m_context, actionDirectInference, ScKeynodes::nrel_result);
      ScAddr solutionNode = IteratorUtils::getAnyFromSet(&m_context, answer);
      ScAddr solutionTreeRoot = IteratorUtils::getAnyByOutRelation(&m_context, solutionNode, ScKeynodes::rrel_1);
      if (solutionTreeRoot.IsValid())
      {
        ScAddr argNode = IteratorUtils::getAnyFromSet(&m_context, solutionTreeRoot);
        ScAddrVector arguments;
        ScIterator3Ptr argIt = m_context.CreateIterator3(argNode, ScType::ConstPermPosArc, ScType::VarNode);
        while (argIt->Next())
        {
          ScAddrVector arguments;
          arguments.push_back(argIt->Get(2));
        }
        logicRuleNode = IteratorUtils::getAnyByOutRelation(&m_context, solutionTreeRoot, ScKeynodes::rrel_1);
      }
    }
  }
  return logicRuleNode;
}

ScAddr StandardMessageReplyAgent::wrapInSet(ScAddr const & addr)
{
  ScAddr set = m_context.GenerateNode(ScType::ConstNodeTuple);
  m_context.GenerateConnector(ScType::ConstPermPosArc, set, addr);
  return set;
}

ScAddr StandardMessageReplyAgent::generatePhraseAgentParametersNode(const ScAddr & messageNode)
{
  ScAddrVector parameters;
  parameters.push_back(messageNode);

  ScAddr authorNode = messageSearcher->getMessageAuthor(messageNode);
  if (authorNode.IsValid())
    parameters.push_back(authorNode);

  ScAddr themeNode = messageSearcher->getMessageTheme(messageNode);
  if (themeNode.IsValid())
    parameters.push_back(themeNode);

  ScAddr parametersNode = m_context.GenerateNode(ScType::ConstNode);
  for (auto & node : parameters)
  {
    if (!m_context.CheckConnector(parametersNode, node, ScType::ConstPermPosArc))
      m_context.GenerateConnector(ScType::ConstPermPosArc, parametersNode, node);
  }

  return parametersNode;
}

void StandardMessageReplyAgent::initFields()
{
  this->langSearcher = std::make_unique<LanguageSearcher>(&m_context, &m_logger);
  this->messageSearcher = std::make_unique<commonModule::MessageSearcher>(&m_context, &m_logger);
  this->messageHandler = std::make_unique<MessageHandler>(&m_context, &m_logger);
}

}  // namespace dialogControlModule
