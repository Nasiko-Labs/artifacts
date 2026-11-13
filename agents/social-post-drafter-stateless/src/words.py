"""Pure: common capitalised words that are not names (sentence openers in hooks, platform words, generic
acronyms), so grounding does not turn them into [name?]. Anything else capitalised must come from the user."""

_WORDS = """
i i'm i'll i've i'd we we're we've we'll our ours us you you're your yours they their them it it's its this that
these those here here's there there's what what's why how who when where which if and or but so because not no yes
the a an of to in on at for from with by about after before over under into out up down just only also still
now today tomorrow yesterday this week month year new next last first one two three ok okay hi hello hey thanks
thank big small more less most least every each all some many few any none nothing something everything
is are was were be been being do does did done have has had can could will would should may might must
let let's lets meet say said says see look looking get got make made take took give gave go going went
why? what? how? want need know think learned learn lesson lessons tip tips mistake mistakes step steps
story question questions idea ideas truth hot take unpopular opinion pro tip bonus ps p.s. tl;dr tldr note
linkedin x twitter thread post posts dm comment comments follow share like repost
ai api apis ceo cto cfo coo vp hr it saas b2b b2c ux ui sso qa devops ml llm faq roi kpi okr okrs q&a
monday's friday's team teams everyone folks people customers customer users user engineers engineering
product products company startup founders founder hiring hire role roles job jobs remote apply join
launch launching launched release released releasing shipped shipping ship introducing announcing
dashboard dashboards feature features update updates version beta preview docs blog webinar event
"""
COMMON = frozenset(_WORDS.split())
