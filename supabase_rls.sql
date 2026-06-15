-- Philosopher OS — Row Level Security Policies
-- Run this in Supabase SQL Editor AFTER all tables are created

-- 1. Users can only access their own org's data
-- Helper function to get current user's org
CREATE OR REPLACE FUNCTION public.current_user_org()
RETURNS UUID AS $$
  SELECT org_id FROM org_members WHERE user_id = auth.uid() LIMIT 1;
$$ LANGUAGE SQL STABLE;

-- ============= LEADS =============
ALTER TABLE leads ENABLE ROW LEVEL SECURITY;

CREATE POLICY leads_org_isolation ON leads
  FOR ALL USING (org_id = public.current_user_org());

CREATE POLICY leads_owner_access ON leads
  FOR SELECT USING (assigned_to = auth.uid() OR created_by = auth.uid()::text);

-- Admins can see all leads in their org
CREATE POLICY leads_admin_access ON leads
  FOR ALL USING (
    EXISTS (SELECT 1 FROM org_members WHERE user_id = auth.uid() AND org_id = leads.org_id AND role = 'admin')
  );

-- ============= CLIENTS =============
ALTER TABLE clients ENABLE ROW LEVEL SECURITY;

CREATE POLICY clients_org_isolation ON clients
  FOR ALL USING (org_id = public.current_user_org());

-- ============= CAMPAIGNS =============
ALTER TABLE campaigns ENABLE ROW LEVEL SECURITY;

CREATE POLICY campaigns_org_isolation ON campaigns
  FOR ALL USING (org_id = public.current_user_org());

CREATE POLICY campaigns_owner_access ON campaigns
  FOR SELECT USING (owner_id = auth.uid());

-- ============= TASKS =============
ALTER TABLE tasks ENABLE ROW LEVEL SECURITY;

CREATE POLICY tasks_org_isolation ON tasks
  FOR ALL USING (org_id = public.current_user_org());

CREATE POLICY tasks_assignee_access ON tasks
  FOR SELECT USING (assignee_id = auth.uid());

-- ============= CONVERSATIONS =============
ALTER TABLE conversations ENABLE ROW LEVEL SECURITY;

CREATE POLICY conversations_org_isolation ON conversations
  FOR ALL USING (org_id = public.current_user_org());

-- ============= MESSAGES =============
ALTER TABLE messages ENABLE ROW LEVEL SECURITY;

CREATE POLICY messages_via_conversation ON messages
  FOR ALL USING (
    EXISTS (SELECT 1 FROM conversations WHERE id = messages.conversation_id AND org_id = public.current_user_org())
  );

-- ============= NOTIFICATIONS =============
ALTER TABLE notifications ENABLE ROW LEVEL SECURITY;

CREATE POLICY notifications_own ON notifications
  FOR ALL USING (user_id = auth.uid());

-- ============= CALENDAR EVENTS =============
ALTER TABLE calendar_events ENABLE ROW LEVEL SECURITY;

CREATE POLICY calendar_org_isolation ON calendar_events
  FOR ALL USING (org_id = public.current_user_org());

-- ============= INVOICES =============
ALTER TABLE invoices ENABLE ROW LEVEL SECURITY;

CREATE POLICY invoices_org_isolation ON invoices
  FOR ALL USING (org_id = public.current_user_org());

-- ============= EXPENSES =============
ALTER TABLE expenses ENABLE ROW LEVEL SECURITY;

CREATE POLICY expenses_org_isolation ON expenses
  FOR ALL USING (org_id = public.current_user_org());

-- ============= REVENUE EVENTS =============
ALTER TABLE revenue_events ENABLE ROW LEVEL SECURITY;

CREATE POLICY revenue_org_isolation ON revenue_events
  FOR ALL USING (org_id = public.current_user_org());

-- ============= AGENT MEMORY =============
ALTER TABLE agent_memory ENABLE ROW LEVEL SECURITY;

CREATE POLICY agent_memory_org_isolation ON agent_memory
  FOR ALL USING (org_id = public.current_user_org());

-- ============= KNOWLEDGE BASE =============
ALTER TABLE knowledge_base ENABLE ROW LEVEL SECURITY;

CREATE POLICY knowledge_org_isolation ON knowledge_base
  FOR ALL USING (org_id = public.current_user_org());

-- ============= INTEGRATIONS =============
ALTER TABLE integrations ENABLE ROW LEVEL SECURITY;

CREATE POLICY integrations_org_isolation ON integrations
  FOR ALL USING (org_id = public.current_user_org());

-- ============= INVITES =============
ALTER TABLE invites ENABLE ROW LEVEL SECURITY;

CREATE POLICY invites_admin_only ON invites
  FOR ALL USING (
    EXISTS (SELECT 1 FROM org_members WHERE user_id = auth.uid() AND org_id = invites.org_id AND role = 'admin')
  );
