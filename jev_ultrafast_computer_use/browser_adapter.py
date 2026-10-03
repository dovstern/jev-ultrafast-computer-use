"""Observe styled native controls through their visible labels."""

import json
import time

from jev_ultrafast import Agent
from jev_ultrafast.browser import StalePage, fingerprint

LABELS = r"""(represented => {
  const cache=window.__jevFast;
  if (!cache) return null;
  const identity=e=>{
    if (!cache.ids.has(e)) cache.ids.set(e,cache.next++);
    const id=cache.ids.get(e); cache.nodes.set(id,e); return id;
  };
  const visible=e=>!e.closest('[aria-hidden="true"],[inert]') &&
    e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true});
  const available_nodes=represented.filter(node=>{
    const e=cache.nodes.get(node);
    if (!e?.isConnected || !visible(e)) return false;
    const r=e.getBoundingClientRect(),x=r.x+r.width/2,y=r.y+r.height/2;
    return r.width>0 && r.height>0 && x>=0 && y>=0 && x<innerWidth && y<innerHeight &&
      e.contains(document.elementFromPoint(x,y));
  });
  const actions=[], guards={}, included=new Set(available_nodes);
  for (const label of document.querySelectorAll('label')) {
    const input=label.control;
    if (!input || !['checkbox','radio'].includes(input.type) || input.matches(':disabled') ||
        input.closest('[aria-disabled="true"],[inert]') || !visible(label)) continue;
    const control_node=identity(input);
    if (included.has(control_node)) continue;
    const r=label.getBoundingClientRect(), x=r.x+r.width/2, y=r.y+r.height/2;
    if (!r.width || !r.height || x<0 || y<0 || x>=innerWidth || y>=innerHeight ||
        !label.contains(document.elementFromPoint(x,y))) continue;
    const node=identity(label), guard=cache.guard(label);
    actions.push({id:'label_'+node,node,control_node,kind:'click',role:input.type,
      label:guard[2] || input.getAttribute('aria-label') || input.type,
      checked:String(input.checked),value:String(input.value),
      rect:{x:r.x,y:r.y,w:r.width,h:r.height}});
    guards[node]=guard;
    included.add(control_node);
  }
  return {actions,guards,available_nodes,page_key:cache.pageKey()};
})"""


class BrowserAdapter:
    def __init__(self, browser):
        self._browser = browser

    def __getattr__(self, name):
        return getattr(self._browser, name)

    @property
    def target(self):
        return self._browser.target

    @target.setter
    def target(self, value):
        self._browser.target = value

    @property
    def session(self):
        return self._browser.session

    @session.setter
    def session(self, value):
        self._browser.session = value

    def observe(self, screenshot=True):
        for attempt in range(20):
            try:
                return self._observe_once(screenshot=screenshot)
            except StalePage:
                if attempt == 19:
                    raise
                time.sleep(0.05)
        raise StalePage("Page did not settle")

    def _observe_once(self, screenshot=True):
        page = self._browser.observe(screenshot=screenshot)
        represented = [action["node"] for action in page["actions"] if "node" in action]
        labels = self._browser.evaluate(LABELS + "(" + json.dumps(represented) + ")")
        if labels is None or labels["page_key"] != page["page_key"] or not self._browser.fresh(page):
            raise StalePage("Page changed while observing native labels")
        available = set(labels["available_nodes"])
        controls = [action for action in page["actions"] if action.get("node") in available]
        room = max(0, 250 - len(controls))
        added = labels["actions"][:room]
        page["actions"] = controls + added + [action for action in page["actions"] if "node" not in action]
        page["guards"].update(labels["guards"])
        page["omitted_actions"] = page.get("omitted_actions", 0) + len(labels["actions"]) - len(added)
        page["fingerprint"] = fingerprint(page)
        return page

    def fresh(self, page, action=None):
        if action is not None and "control_node" in action:
            association = self._browser.evaluate(
                "(action=>{const c=window.__jevFast,label=c?.nodes.get(action.node),"
                "input=c?.nodes.get(action.control_node);return !!input && label?.control===input &&"
                "input.isConnected && !input.matches(':disabled') &&"
                "!input.closest('[aria-disabled=\"true\"],[inert]');})(" + json.dumps(action) + ")"
            )
            if not association:
                return False
        return self._browser.fresh(page, action)

    def act(self, action, page, text=None):
        if "control_node" in action and not self.fresh(page, action):
            raise StalePage("The label's native control changed before input")
        return self._browser.act(action, page, text=text)


def create_agent(url, goal):
    agent = Agent(url, goal)
    adapter = BrowserAdapter(agent.browser)
    agent.browser = agent.state["browser"] = adapter
    try:
        agent.state["page"] = adapter.observe(screenshot=agent.screenshots)
    except Exception:
        adapter.close()
        raise
    return agent
