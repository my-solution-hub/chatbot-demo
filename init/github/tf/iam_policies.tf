# iam_policies.tf — Policies attached to the GitHub Actions role

# AdministratorAccess for demo — replace with scoped policies for production
resource "aws_iam_role_policy_attachment" "admin_access" {
  role       = aws_iam_role.github_actions.name
  policy_arn = "arn:aws:iam::aws:policy/AdministratorAccess"
}
