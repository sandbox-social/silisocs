import json


class AgentModel:
    """
    An agent model class for an exogenous (i.e. fixed/external to the sim) posting schedule and content
    """

    def __init__(self, name, posts):
        self._agent_name = name
        self.posts = posts
        self.used_posts = set()
        self.current_post_index = 0
        self.seed_post = ""

    def post(self, app):
        post = self.generate_post()
        media = [img_filepath for img_filepath in self.posts[post]]
        if len(media) > 0:
            app.post_toot(self._agent_name, status=post, media_links=media)
        else:
            app.post_toot(self._agent_name, status=post)
        return post

    def generate_post(self):
        # Get next unused post
        while self.current_post_index < len(self.posts):
            post = list(self.posts.keys())[self.current_post_index]
            self.current_post_index += 1
            if post not in self.used_posts:
                self.used_posts.add(post)
                return post

        # Reset if we've gone through all posts
        self.current_index = 0
        self.used_posts.clear()
        return self.posts[0]  # Start over with first post


class AgentBuilder:
    """
    An agent class for an exogenous (i.e. fixed/external to the sim) posting schedule and content
    """

    @classmethod
    def build(cls, *, name="", posts={}) -> AgentModel:
        """Build an exogenous agent.
        Args:
            config: The agent config to use.

        Returns
        -------
            An agent.
        """
        # parse settings
        agent = AgentModel(
            name=name,
            posts=posts,
        )
        return agent


def save_agent_to_json(
    agent: AgentModel,
) -> str:
    """
    Saves an agent to JSON data.
    """
    data = {}
    data["current_post_index"] = agent.current_post_index
    data["used_posts"] = agent.used_posts
    data["name"] = agent._agent_name
    data["posts"] = agent.posts
    return json.dumps(data)


def rebuild_from_json(
    json_data: str,
) -> AgentModel:
    """Rebuilds an agent from JSON data."""
    data = json.loads(json_data)
    # parse settings
    agent = AgentModel(
        name=data["agent_name"],
        posts=data["posts"],
    )
    agent.current_post_index = data["current_post_index"]
    agent.used_posts = data["used_posts"]
    return agent
